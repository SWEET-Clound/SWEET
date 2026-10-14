# -*- coding: utf-8 -*-
"""
Author: HAOXINDONG
Date: 2023-04-26 14:42:39
LastEditors: HAOXINDONG
LastEditTime: 2025-06-23 20:02:25
FilePath: /qll_chat_staging/qll_chat/chat.py
"""
# from qll_chat.workflow_chat import WorkflowChat
import copy
import hashlib
import json
import logging
import os
import re
import time
import traceback
import sseclient
import requests
from datetime import datetime
from enum import Enum
from queue import Full, Queue
from threading import Lock, Thread

from langdetect import DetectorFactory
from pymongo.errors import DuplicateKeyError

from project_utils.generic_utils import b64_handler
from project_utils.generic_utils.common import track_vars, beautify
from project_utils.generic_utils.exception_utils import UtilsExceptionBase, UtilsException
from project_utils.generic_utils.service_api_utils import service_fn
from project_utils.text_process.file_operation import dump_json, load_json
from project_utils.generic_utils.stream_utils import bridge_async_iter
from qll_chat.demo_data import demo_data
# from qll_chat.intention_chat import IntentionChat as IntentionChatV1
from qll_chat.support_modules.reference_document import ReferenceDocument
from qll_chat.intention_chat import IntentionChat as IntentionChatV2
# from qll_chat.intention_cls import OAIntention
from qll_chat.intention_cls import IntentionCLS, DirectIntentionCLS, SysIntentionCLS
from qll_chat.oa_llm_chat.utils import oa_result2markdown
from qll_chat.prompts.biss_rule_check import BissRuleCheck
from qll_chat.prompts.dialog_build_agent import DialogBuildAgent
from qll_chat.prompts.dialog_build_parent_agent import DialogBuildParentAgent
from qll_chat.prompts.knowledge_qa import KnowledgeQAPrompt
from qll_chat.prompts.openclaw import OpenClawPrompt
from qll_chat.prompts.person_recommend import PersonRecommend
from qll_chat.prompts.question_connection_judge import QuestionConnectionJudge
from qll_chat.prompts.second_dev import SecondDevPrompt
from qll_chat.prompts.writing import WritingPrompt
from qll_chat.prompts.long_doc_process_agent import LongDocProcess
from qll_chat.prompts.phone_call import PhoneCall
from qll_chat.settings import BOT_SETTINGS
from qll_chat.sim_qa import SimQA
from qll_chat.utils.data_process import model_info_preprocess as common_model_info_preprocess
from qll_chat.utils.oa_utils.utils import ConversationStatus, Status
from qll_chat.application.avatar import AvatarPrompt
from qll_chat.agents.avatar_agent import AvatarEMAgent, AvatarPageAgent
from qll_chat.agents.generic_agent import GenericAgent
from qll_chat.agents.voice_agent import VoiceAgent
from qll_chat.agents.customer_qa_agent import CustomerQAAgent
from qll_chat.agents.mcp_agent import MCPAgent
from qll_chat.agents.oa_agent import OAAgent
from qll_chat.agents.client_oa_agent import ClientOAAgent
from qll_chat.agents.mixins.mcp_citation import MCP_CITATION_RUNTIME_KEYS
from qll_chat.mcp.reference_meta import merge_corpus_extra_file_fields
from qll_chat.sandbox.agent_runtime import build_sandbox_agent_runtime
from qll_chat.skills.agent_extension import build_skill_agent_extension
from qll_chat.agents.capabilities import is_search_request
from qll_chat.sandbox.routing import OA_AGENT_SANDBOX_TOOL_ROUTES
from qll_chat.utils.local_log_cleanup import DEFAULT_LOCAL_DIAGNOSTIC_LOG_DIR
from qll_chat.utils.param_parser import parse_bool
from qll_chat.utils.search_range_validation import validate_network_config_when_enabled

# 为了保证每次检测结果的一致性
DetectorFactory.seed = 0

logger = logging.getLogger(__name__)

# MongoDB 单文档硬上限为 16 MiB。诊断文档预留 4 MiB 安全空间，并限制
# _llm_request_history 自身最多占 4 MiB；该字段每轮保存完整 messages，是目前
# 文档随模型调用轮次增长的主要来源。
MONGO_DIAGNOSTIC_TARGET_BYTES = 12 * 1024 * 1024
MONGO_REQUEST_HISTORY_MAX_BYTES = 4 * 1024 * 1024

# 客户端断开日志（前台点「停止」）的写入队列上限。
#
# 为什么必须有界：每个待写快照都是一份完整请求（含 _llm_request_history）。
# Mongo 阻塞时写入侧可能停滞，
# 若每次断开都新起线程，线程与快照会无上限堆积直至拖垮 worker。这里改为
# 进程内单线程 + 有界队列，队满即丢弃并计数告警 —— 断开日志是旁路记录，
# 不能为了它牺牲主流程。
#
# 入队前先按 Mongo 规则裁剪，并按裁剪后的实际 JSON 字节数做总量控制；数量和
# 字节数必须同时受限，不能把 12MiB 的 Mongo 目标值误当成原始请求大小上限。
_DISCONNECT_LOG_QUEUE_SIZE = 16
_DISCONNECT_LOG_QUEUE_MAX_BYTES = 192 * 1024 * 1024

# {"queue": Queue, "thread": Thread}，进程内懒初始化单例。
_disconnect_log_writer = None
_disconnect_log_writer_lock = Lock()
_disconnect_log_stats = {"queued": 0, "written": 0, "failed": 0, "dropped": 0}
_disconnect_log_stats_lock = Lock()
_disconnect_log_pending_bytes = 0
_disconnect_log_pending_bytes_lock = Lock()


def _bump_disconnect_log_stat(key, delta=1):
    with _disconnect_log_stats_lock:
        _disconnect_log_stats[key] = _disconnect_log_stats.get(key, 0) + delta


def _enqueue_disconnect_log(q, item, snapshot_bytes):
    """同时执行数量和字节预算检查，并为成功入队的快照预留内存额度。"""
    global _disconnect_log_pending_bytes
    with _disconnect_log_pending_bytes_lock:
        if (
            snapshot_bytes > _DISCONNECT_LOG_QUEUE_MAX_BYTES
            or _disconnect_log_pending_bytes + snapshot_bytes
            > _DISCONNECT_LOG_QUEUE_MAX_BYTES
        ):
            raise Full
        # 持锁 put，避免 worker 先取走并释放额度、生产者随后才增加额度。
        q.put_nowait(item)
        _disconnect_log_pending_bytes += snapshot_bytes


def _release_disconnect_log_bytes(snapshot_bytes):
    global _disconnect_log_pending_bytes
    with _disconnect_log_pending_bytes_lock:
        _disconnect_log_pending_bytes = max(
            0, _disconnect_log_pending_bytes - snapshot_bytes
        )


def _disconnect_log_worker(q):
    """串行消费客户端断开日志。

    单线程既限定了写入侧的资源占用（Mongo 阻塞时最多滞留在队列里的那些），
    也保证同一 answerId 的写入按入队顺序落地。
    """
    while True:
        chat, snapshot, answer_id, saved_at, snapshot_bytes = q.get()
        try:
            outcome = chat._save_mongo_log(
                snapshot,
                overwrite=ClientOAAgent._is_client_request(snapshot),
                log_saved_at=saved_at,
                raise_on_total_failure=True,
            )
            _bump_disconnect_log_stat("written")
            logger.info(
                '答案ID：%s;客户端断开日志写入完成 | outcome=%s',
                answer_id,
                outcome,
            )
        except Exception:
            _bump_disconnect_log_stat("failed")
            logger.error(
                '答案ID：%s;客户端断开日志写入失败', answer_id, exc_info=True
            )
        finally:
            _release_disconnect_log_bytes(snapshot_bytes)
            q.task_done()


def _get_disconnect_log_writer():
    """懒初始化进程内唯一的写入队列与消费线程。

    必须懒初始化而不是模块导入期创建：gunicorn 以 fork 启动 worker，导入期
    创建的线程不会被继承（且线程 + fork 本身有死锁风险）。
    """
    global _disconnect_log_writer
    if _disconnect_log_writer is not None:
        return _disconnect_log_writer
    with _disconnect_log_writer_lock:
        if _disconnect_log_writer is None:
            q = Queue(maxsize=_DISCONNECT_LOG_QUEUE_SIZE)
            thread = Thread(
                target=_disconnect_log_worker,
                args=(q,),
                daemon=True,
                name="client-disconnect-log-writer",
            )
            thread.start()
            _disconnect_log_writer = {"queue": q, "thread": thread}
    return _disconnect_log_writer


class BaseChat(object):
    def __init__(self, config) -> None:
        self.config = config
        self.error_code = BOT_SETTINGS.error_code
        self.political_keywords = re.compile(
            r"台湾问题|共产党|习近平|国家主席|Taiwan|communist party"
        )
        self.political_answer = "作为一个AI助手，我无法回答关于政治敏感的话题，请问您有其他需要帮助的问题吗？"
        self.punctuation_answer = "抱歉，我不明白您的意思，请问您有其他需要帮助的问题吗？"
        self.weaver_tenantId = ["100002", "100381"]
        self.company_name = config.get("company_name")

    def political_sensitive(self, query):
        if self.political_keywords.search(query):
            return True
        else:
            return False

    def content_stream(self, content, questionId, usage, extra_content=None, function_name=None, req={}):
        # extra_content 为流传输结束后返回的额外数据，或用于不需要返回文本的情况
        answer = ""
        num = 1
        function_name = function_name if function_name else req.get("functionName")
        for ind in range(len(content) + 1):
            tmp = dict()
            tmp["questionId"] = questionId
            # if function_name in ["sceneChat", "generalChat"]:

            tmp["extraData"] = {"functionName": function_name}
            if ind < len(content):
                tmp["seq"] = num
                tmp["status"] = "0"
                tok = content[ind]
                tmp["token"] = tok
                answer += tok
                num += 1
            else:
                tmp["seq"] = num
                tmp["status"] = "1"
                tmp["token"] = ""
                if extra_content is not None:
                    extra_content["functionName"] = function_name
                    tmp["extraData"] = extra_content  # 返回额外数据
                tmp['total_tokens'] = usage['total_tokens']
                tmp['llm_model'] = usage['llm_model']
                if req.get("channel", None) == "writer" and function_name == "writingConfig":
                    tmp["extraData"]["writerArrange"] = {
                        "sessionId": req.get("sessionId"),
                        "sessionStepId": req.get("sessionStepId"),
                        "identifySessionStep": "agentOver",
                        "nextConditon": 0
                    }
                logger.info(f"Bot回答 token数量：{num}")
                logger.info(f"Bot回答：{answer}")
            cur_token = "data: " + json.dumps(tmp, ensure_ascii=False) + "\n\n"
            # logger.info(f"cur_token:{cur_token}")
            yield cur_token

    def is_all_punctuation(self, text):
        """
            1. ^[{re.escape(punctuation)}]+$：^ 和 $ 是正则表达式中的锚定符，表示字符串的开始和结束。这确保了整个字符串都需要满足接下来的条件。

            2. {re.escape(punctuation)}：re.escape() 函数在 punctuation 字符串中的所有特殊字符（例如，*, +, ?, {, } 等）前面添加了转义符 \。这是因为正则表达式中的某些字符具有特殊意义，如果我们要在模式中使用它们作为普通字符，就需要对它们进行转义。在这个例子中，我们将所有标点符号视为普通字符，所以需要对它们进行转义。
        """

        # 定义一个包含常见标点符号的字符串
        punctuation = r"""!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~。，、；：？！‘’“”【】《》（）…—"""

        # 使用正则表达式检查字符串是否全部由标点符号组成
        pattern = f"^[{re.escape(punctuation)}]+$"
        match = re.match(pattern, text)
        return bool(match)

    @track_vars
    def query_regex_rephrase(self, req):
        pattern = r"你们公司|我们公司|贵司"
        query = req["question"]
        if re.search(pattern, query):
            if "algExtra" in req and "personalSetting" in req["algExtra"]:
                personalSetting = req["algExtra"]["personalSetting"]
                company_name = personalSetting.get("tenantName", self.company_name)
                query = re.sub(pattern, company_name, query)
                req["question"] = query

    def model_info_preprocess(self, model_info):
        return common_model_info_preprocess(model_info)

    def detect_history_language(self, req=None):
        """
        目前整体背景信息、prompt语言都是中文，如果没有history或者history都是中文&当前提问也是中文，那么就没必要在prompt中请使用中文回复我
        """
        history = req.get("history")
        question = req.get("question")
        if not history or "翻译" in question:
            return "中文"
        latest_answer = history[-1]["answer"]
        latest_answer_lang = self.detect_language(latest_answer, req)
        return latest_answer_lang

    def detect_language(self, text, req=None):
        """
        语言代码表：http://www.lingoes.cn/zh/translator/langcode.htm
        英文：en
        langdetect 简体中文：zh-cn
        langid 简体中文：zh  （采用 ISO 639-1 codes） https://en.wikipedia.org/wiki/List_of_ISO_639_language_codes

        注意：有一些语言是没法按照判断字符 来判断是否包含对应语言字符的  比如 印尼语都是字母  日文中也会包含一些中文字符（中文中也可能会包含一些日文字符）

        需要指定回复语言的原因是：大部分租户数据都是中文、prompt也是中文，对于第一语言是其他语言的用户如果不显式指定回复语言，可能会会回复中文，但是对于第一语言是中文的用户不需要显式的指定

        2024.08版本
        0. 任何异常情况都按照中文为默认语言回复
        1. 如果提问中包含第一语言或全是数字 使用第一语言回复
        2. 如果提问中不包含第一语言字符 使用模型判断
        2024.12版本
        1. 回复语言优先使用第一语言（在背景知识、prompt都是中文的大前提下，如果第一语言是中文，不需要在prompt中进行指定）
        2. 如果没有传第一语言，暂时不做回复语言的限定（微搜端问答目前没有传，但是提问会有E10这种 如果采用模型判断为英文回复也不合适）
        """
        lang_map = {
            "zh-CN": {"name": "中文", "regex": "[\u4e00-\u9fff]"},
            "zh": {"name": "中文", "regex": "[\u4e00-\u9fff]"},
            "ja": {"name": "日语", "regex": "[\u3040-\u309F\u30A0-\u30FF\uFF66-\uFF9F\u3000-\u303F]"},
            "th": {"name": "泰语", "regex": "[\u0E00-\u0E7F]"},
            "ko": {"name": "韩语", "regex": "[\uAC00-\uD7AF\u1100-\u11FF]"},
            "vi": {"name": "越南语", "regex": "[ĂăÂâÊêÔôƠơƯưĐđ]"},
            "id": {"name": "印度尼西亚语"},
            "en": {"name": "英语"}
        }
        try:
            language = "zh-CN"  # "zh-CN" "en" 未识别到用户传的第一语言的默认语言
            acceptLanguage = req.get("algExtra", {}).get("regionLanguage", {}).get("acceptLanguage", "")
            logger.info(f"acceptLanguage: {acceptLanguage}")
            if acceptLanguage:
                first_lang = acceptLanguage.split(",")[0].split(";")[0]
                if first_lang in lang_map:
                    language = first_lang
                    return lang_map[language]["name"]

            # # 如果用户输入都是emoji表情，就按照用户的第一语言进行回复
            # if bool(EMOJI_PATTERN.fullmatch(text)):
            #     return lang_map[language]["name"]

            # # logger.debug(language)
            # # logger.debug(text)
            # text = remove_emoji(text)  # 去除输入文本中的emoji 避免影响语言的判断
            # if lang_map[language].get("regex", None):
            #     # logger.debug(lang_map[language]["regex"])
            #     # logger.debug(bool(re.search(lang_map[language]["regex"], text)))
            #     # logger.debug(bool(re.fullmatch(r"[0-9\s!、\"#$%&()*+,-./:;<=>?@[\\]^_`{'|}~，。（）【】’]*", text)))
            #     if bool(re.search(lang_map[language]["regex"], text)) or bool(
            #             re.fullmatch(r"[0-9\s!！、\"#$%&()*+,-./:;<=>?？@[\\\]^_`{'|}~，。（）【】’]*", text)):
            #         return lang_map[language]["name"]

            # try:
            #     # 如果设置的第一语言与模型判断的不一致 应该是按照模型判断的语言进行回复
            #     pred_language, confidence = langid.classify(text)
            #     if pred_language in lang_map:
            #         return lang_map[pred_language]["name"]
            #     elif bool(re.fullmatch("[a-zA-Z0-9\s]*", text)):
            #         return lang_map["en"]["name"]
            #     else:
            #         return lang_map[language]["name"]
            # except Exception as e:
            #     logger.error(str(e))
            #     return "中文"
        except Exception as e:
            logger.error(str(e))
            return "中文"


class QAChat(BaseChat):
    def __init__(self, xiaoe_chat_config, config, xiaoe_agent_config, private=False) -> None:
        super(QAChat, self).__init__(config)
        self.writing = WritingPrompt(xiaoe_chat_config, config, private=private)
        self.avatar = AvatarPrompt(xiaoe_chat_config, config)
        self.long_doc_agent = LongDocProcess(xiaoe_chat_config, config)
        self.phone_call = PhoneCall(xiaoe_chat_config, config)
        self.second_dev = SecondDevPrompt(xiaoe_chat_config, config)
        self.person_recommend = PersonRecommend(xiaoe_chat_config,config,xiaoe_agent_config,)
        self.knowledge_qa = KnowledgeQAPrompt(xiaoe_chat_config, config, private=private)
        self.openclaw = OpenClawPrompt(xiaoe_chat_config, config)
        self.simqa = SimQA(config)
        self.qa_build_agent = DialogBuildAgent(xiaoe_chat_config, config)
        self.qa_build_parent_agent = DialogBuildParentAgent(xiaoe_chat_config, config)
        self.biss_rule_check = BissRuleCheck(xiaoe_chat_config)
        self.es_info_url = config["es_info_url"]
        self.saveReferenceData_fn = service_fn(url=config.get("saveReferenceData_url", None),
                                               service_name="saveReferenceData", timeout=10)
        self.get_corpus_info_fn = service_fn(
            url=config.get("get_corpus_info_url", None),
            service_name="get_corpus_info",
            timeout=10,
        )
        self.get_model_info_fn = service_fn(
            url=xiaoe_chat_config["get_model_info"],
            service_name="get_model_info",
            timeout=10,
        )
        self.get_user_mcps_fn = service_fn(
            url=config["get_user_mcps_url"],
            service_name="get_user_mcps",
            timeout=10,
        )

        self.mcp_agent = MCPAgent(
            config=xiaoe_agent_config,
            xiaoe_chat_config=xiaoe_chat_config,
            qa_config=config
        )
        self.oa_agent = OAAgent(
            config=xiaoe_agent_config,
            xiaoe_chat_config=xiaoe_chat_config,
            qa_config=config
        )
        # Skill 挂载到 SaaS OAAgent 和 GenericAgent；客户端继续使用自己的 Skill/terminal 协议。
        skill_extension = build_skill_agent_extension(xiaoe_agent_config)
        if skill_extension is not None:
            self.oa_agent.set_skill_extension(skill_extension)
        # SaaS Agent 的 Python 计算、命令和产物工具统一走 Sandbox Runtime。
        sandbox_runtime = build_sandbox_agent_runtime(
            xiaoe_agent_config,
            trusted_tool_routes=OA_AGENT_SANDBOX_TOOL_ROUTES,
        )
        if sandbox_runtime is not None:
            self.oa_agent.set_tool_execution_extension(sandbox_runtime)
        self.client_oa_agent = ClientOAAgent(
            config=xiaoe_agent_config,
            xiaoe_chat_config=xiaoe_chat_config,
            qa_config=config
        )
        self.generic_agent = GenericAgent(
            config=xiaoe_agent_config,
            xiaoe_chat_config=xiaoe_chat_config,
            qa_config=config
        )
        self.voice_agent = VoiceAgent(
            config=xiaoe_agent_config,
            xiaoe_chat_config=xiaoe_chat_config,
        )
        if skill_extension is not None:
            self.generic_agent.set_skill_extension(skill_extension)
        if sandbox_runtime is not None:
            self.generic_agent.set_tool_execution_extension(sandbox_runtime)
        self.customer_qa_agent = CustomerQAAgent(
            config=xiaoe_agent_config,
            xiaoe_chat_config=xiaoe_chat_config,
            qa_config=config
        )
        self.avatar_page_agent = AvatarPageAgent(
            config=xiaoe_agent_config,
            xiaoe_chat_config=xiaoe_chat_config,
            qa_config=config
        )
        self.avatar_em_agent = AvatarEMAgent(
            config=xiaoe_agent_config,
            xiaoe_chat_config=xiaoe_chat_config,
            qa_config=config
        )
        self.xiaoe_agent_url = config["xiaoe_agent_url"]
        self.mcp_developer_mode = config["mcp_developer_mode"]

        self.reference_documents = None
        if config["reference_document"]:
            self.reference_documents = ReferenceDocument(config)
        self.not_encryption_user = []
        self.private = private
        self.mongodb_op = None
        self.mongo_collection_name = config.get("db", {}).get("mongodb", {}).get("collection_name")
        self.chatlog2localfile = config["chatlog2localfile"]
        self.local_chatlog_dir = str(config.get("chatlog_local_dir") or DEFAULT_LOCAL_DIAGNOSTIC_LOG_DIR)
        if not private:
            from project_utils.db.mongodb_operation import MongoDBOperation
            self.mongodb_op = MongoDBOperation(**config["db"]["mongodb"])

    @staticmethod
    def _safe_file_part(value):
        text = str(value or "").strip()
        if not text:
            return ""
        return re.sub(r"[^0-9A-Za-z_.-]+", "_", text)[:120]

    def _build_chatlog_file_path(self, req):
        answer_id = self._safe_file_part(req.get("answerId"))
        question_id = self._safe_file_part(req.get("questionId"))
        if answer_id:
            filename = f"{answer_id}.json"
        elif question_id:
            filename = f"{question_id}.json"
        else:
            filename = f"chat_{int(time.time() * 1000)}.json"
        return os.path.join(self.local_chatlog_dir, filename)

    def save_log2local_file(self, req, force=False):
        if not force and not self.chatlog2localfile:
            return ""
        file_path = self._build_chatlog_file_path(req)
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(
                req,
                f,
                ensure_ascii=False,
                separators=(",", ":"),
                default=lambda o: list(o) if isinstance(o, set) else str(o)
            )
        return file_path

    def get_model_info(self, req):
        payload = {
            "tenantId": req.get("tenantId", None) or "0",
            "userId": req.get("userId", None) or "0",
            "channel": req.get("channel", None) or "0",  # qa  work  writer analysis
            "code": req.get("llm_model", None)  # 模型code
        }
        model = req.get("llm_model", None)
        try:
            response = self.get_model_info_fn(**payload)
            if response["code"] == "0000":
                model_info = json.loads(b64_handler.decode(response["data"][8:])).get("model_info")
                req["model_info"] = model_info
            else:
                raise UtilsExceptionBase(6209, self.error_code[6209] + f'{model}模型 model_info 获取失败{str(response)}')
        except (UtilsExceptionBase, UtilsException) as ue:
            logger.error(f"获取 {model} model_info失败: {str(ue)}")
            raise
        except Exception as e:
            logger.error(f"获取 {model} model_info失败")
            logger.error(str(e))
            raise UtilsExceptionBase(6209, self.error_code[6209] + f'{model}模型 model_info 获取失败{str(e)}')

    def get_user_mcps(self, req):
        answerId = req.get("answerId", None)
        if is_search_request(req):
            logger.info("answerId:%s channel=aiSearch, skip get_user_mcps", answerId)
            return

        # activeScope 指定了 MCP：用户已在前台 @ 明确限定使用范围，
        # 直接使用指定 MCP，不再调接口检索用户有权限的 MCP 列表。
        active_mcp_ids = ClientOAAgent._active_scope_mcp_ids(req)
        if active_mcp_ids:
            req["mcp_ids"] = active_mcp_ids
            req["_active_scope_mcp_ids"] = list(active_mcp_ids)
            logger.info(
                "answerId:%s activeScope 指定 MCP %s，直接使用，跳过 get_user_mcps 接口检索",
                answerId,
                active_mcp_ids,
            )
            return

        scene_mcp_enable = req.get("algExtra", {}).get("sceneMcp", {}).get("enable")

        # 如果已经有 mcp_ids，直接返回
        if "mcp_ids" in req:
            return

        if scene_mcp_enable != 1:
            # 指定了知识库标签不走MCP
            tagList = req.get("algExtra", {}).get("tagList", [])
            if tagList:
                logger.info(f"answerId:{answerId}指定了知识库标签{tagList}，不走MCP")
                return

            # 指定了2nd_dev 不走MCP
            functionName = req.get("functionName", None)
            if functionName == "2nd_dev":
                logger.info(f"answerId:{answerId}指定了2nd_dev函数，不走MCP")
                return

            # 指定了文件列表不走MCP（暂时方案）
            fileList = req.get("algExtra", {}).get("fileList", None)
            if fileList:
                logger.info(f"answerId:{answerId}指定了文件列表{fileList}，不走MCP")
                return

            logger.info(f"hit_qa: {req.get('hit_qa')}")
            if req.get("hit_qa", {}).get("std_qid"):
                logger.info(f"answerId:{answerId}命中了hit_qa，不走MCP")
                return

        payload = {
            "tenantId": req.get("tenantId") or "0",
            "userId": req.get("userId") or "0",
        }

        if scene_mcp_enable == 1:
            payload["isMcpWork"] = True

        try:
            response = self.get_user_mcps_fn(**payload)

            # 正常返回
            if response.get("code") == "0000":
                user_mcps = response.get("data", []) or []
                req["mcp_ids"] = [item.get("id") for item in user_mcps]
                logger.info(f"用户ID：{req.get('userId')} 获取人员MCP信息成功，返回可用MCP ID列表: {req['mcp_ids']}")
                return

            # 接口返回非0000，记录 warning 但不影响后续流程
            logger.warning(f"获取人员MCP信息失败，返回非0000: {response}")
            req["mcp_ids"] = []
            return

        except Exception as e:
            # 接口不存在 / 未部署 / 网络错误 直接降级，不抛异常
            logger.warning(f"获取人员MCP信息异常（已忽略，不影响后续流程）: {str(e)}")
            req["mcp_ids"] = []
            return

    def req_postprocess(self, req):
        req["response_time"] = datetime.now()
        # 一次性清理无用字段
        for key in (
            "context_id2chunks",
            "messages",
            "backend_info_text",
            "raw_history",
            "_encryption_policy",
            "_encryption_context",
        ):
            req.pop(key, None)
        for key in MCP_CITATION_RUNTIME_KEYS:
            req.pop(key, None)
        for query in req.get("queries_info", {}).get("retrieval_queries", {}).values():
            query.pop("embedding", None)

        # image_payloads 体积裁剪：base64 / base64_url 截断，避免日志过大
        def _shorten(val, limit=30):
            if not isinstance(val, str):
                return val
            return val[:limit]

        if "image_payloads" in req:
            for payload in req["image_payloads"] or []:
                if "base64" in payload:
                    payload["base64"] = _shorten(payload["base64"])
                if "base64_url" in payload:
                    payload["base64_url"] = _shorten(payload["base64_url"])

        # _llm_request_history 记录了每一轮 LLM 请求 payload，其中输入框多模态文件
        # 及工具截图会以 data:base64 形式塞进 message 的 image_url。
        # 一个文档转成上百张图片时，这些 base64 会逐轮累积，导致日志体积爆炸；
        # 这里统一缩减图片数据，只保留 mime 前缀，供排障确认图片存在即可。
        if "_llm_request_history" in req:
            self._shrink_request_history_images(req["_llm_request_history"])

        return req

    def _log_private_diagnostic_doc(self, req, collection_name=None, outcome=None, file_path=None, inline_doc=None):
        if not self.private:
            return
        if file_path:
            logger.info(
                "[PRIVATE_DIAGNOSTIC_LOG] 私有化请求诊断日志已保存 | collection=%s | answerId=%s | questionId=%s | outcome=%s | file=%s",
                collection_name or "",
                req.get("answerId", ""),
                req.get("questionId", ""),
                outcome or "",
                file_path,
            )
            return
        logger.error(
            "[PRIVATE_DIAGNOSTIC_LOG] 私有化请求诊断日志落盘失败，已内联输出 | collection=%s | answerId=%s | questionId=%s | outcome=%s | doc=%s",
            collection_name or "",
            req.get("answerId", ""),
            req.get("questionId", ""),
            outcome or "",
            inline_doc if inline_doc is not None else json.dumps(req, ensure_ascii=False, default=str),
        )

    def _save_private_diagnostic_doc(self, req, outcome):
        file_path = ""
        inline_doc = None
        try:
            self._merge_client_resume_diagnostics_from_local(req)
            file_path = self.save_log2local_file(req, force=True)
        except Exception:
            logger.error(
                "私有化请求诊断日志落盘失败 | answerId=%s | questionId=%s | outcome=%s",
                req.get("answerId", ""),
                req.get("questionId", ""),
                outcome,
                exc_info=True,
            )
            try:
                inline_doc = json.dumps(req, ensure_ascii=False, default=str)
            except Exception:
                inline_doc = str(req)
        self._log_private_diagnostic_doc(
            req,
            collection_name=self.mongo_collection_name,
            outcome=outcome,
            file_path=file_path,
            inline_doc=inline_doc,
        )
        return file_path

    @staticmethod
    def _diagnostic_json_size(value):
        """返回诊断数据的 UTF-8 JSON 估算大小。"""
        return len(json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=lambda o: list(o) if isinstance(o, set) else str(o),
        ).encode("utf-8"))

    @classmethod
    def _compact_mongo_diagnostic_history(
        cls,
        req,
        request_history_max_bytes=MONGO_REQUEST_HISTORY_MAX_BYTES,
        target_bytes=MONGO_DIAGNOSTIC_TARGET_BYTES,
    ):
        """限制 Mongo 副本中的 LLM 请求历史体积，保留首轮和最新轮次。

        每个 _llm_request_history 元素都是当轮完整请求，天然会随 messages 增长，
        因此不能靠内容去重控制体积。这里只压缩用于 Mongo 的深拷贝；原始 req
        仍可完整写入本地兜底日志。省略数量会跨客户端续跑累计。
        """
        history = req.get("_llm_request_history")
        if not isinstance(history, list) or not history:
            return req

        previous_meta = req.get("_llm_request_history_compaction") or {}
        previous_omitted = int(previous_meta.get("omitted_count") or 0)

        doc_without_history = dict(req)
        doc_without_history["_llm_request_history"] = []
        doc_without_history.pop("_llm_request_history_compaction", None)
        remaining_doc_budget = max(
            0,
            int(target_bytes) - cls._diagnostic_json_size(doc_without_history),
        )
        history_budget = min(int(request_history_max_bytes), remaining_doc_budget)

        item_sizes = [cls._diagnostic_json_size(item) + 1 for item in history]
        kept_indexes = []
        used_bytes = 2  # JSON list brackets

        # 最新轮包含当前完整 messages，优先保证故障现场可回放。
        latest_index = len(history) - 1
        if item_sizes[latest_index] + used_bytes <= history_budget:
            kept_indexes.append(latest_index)
            used_bytes += item_sizes[latest_index]

        # 首轮包含初始工具集，预算允许时一并保留。
        if latest_index > 0 and item_sizes[0] + used_bytes <= history_budget:
            kept_indexes.append(0)
            used_bytes += item_sizes[0]

        # 再从最新轮向前保留连续后缀，避免留下中间断裂的调用链。
        newest_indexes = []
        for index in range(len(history) - 2, 0, -1):
            item_size = item_sizes[index]
            if used_bytes + item_size > history_budget:
                break
            newest_indexes.append(index)
            used_bytes += item_size
        kept_indexes.extend(newest_indexes)
        kept_indexes = sorted(set(kept_indexes))
        if len(kept_indexes) == len(history):
            if previous_omitted:
                req["_llm_request_history_compaction"] = {
                    "omitted_count": previous_omitted,
                    "retained_count": len(history),
                }
            return req

        dropped_count = len(history) - len(kept_indexes)
        req["_llm_request_history"] = [history[index] for index in kept_indexes]
        req["_llm_request_history_compaction"] = {
            "omitted_count": previous_omitted + dropped_count,
            "retained_count": len(kept_indexes),
        }
        return req

    @staticmethod
    def _merge_client_resume_diagnostics(req, previous):
        """Accumulate diagnostic data for one client answer across interrupt resumes."""
        if not (req.get("_client_tool_resume") or ClientOAAgent._is_client_tool_resume(req)) or not isinstance(previous, dict):
            return req

        for key in ("_llm_request_history", "_llm_response_stream_history", "_llm_cache_records"):
            old_items = previous.get(key)
            new_items = req.get(key)
            if isinstance(old_items, list) and old_items:
                req[key] = copy.deepcopy(old_items) + (
                    list(new_items) if isinstance(new_items, list) else []
                )
        QAChat._compact_client_request_history_images(req.get("_llm_request_history"))
        QAChat._shrink_request_history_images(req.get("_llm_request_history"))

        old_compaction = previous.get("_llm_request_history_compaction") or {}
        new_compaction = req.get("_llm_request_history_compaction") or {}
        omitted_count = int(old_compaction.get("omitted_count") or 0) + int(
            new_compaction.get("omitted_count") or 0
        )
        if omitted_count:
            req["_llm_request_history_compaction"] = {
                "omitted_count": omitted_count,
                "retained_count": len(req.get("_llm_request_history") or []),
            }

        # 合并后重排 cache 记录序号，保持 1..N 连续（不依赖 execution_trace 是否存在）
        for idx, _rec in enumerate(req.get("_llm_cache_records") or [], start=1):
            if isinstance(_rec, dict):
                _rec["seq"] = idx

        old_trace = previous.get("execution_trace")
        new_trace = req.get("execution_trace")
        if isinstance(old_trace, dict) and not isinstance(new_trace, dict):
            req["execution_trace"] = copy.deepcopy(old_trace)
            return req
        if not isinstance(old_trace, dict) or not isinstance(new_trace, dict):
            return req

        merged_trace = copy.deepcopy(new_trace)
        list_keys = (
            "lifecycle_events",
            "llm_rounds",
            "tool_calls",
            "dynamic_search_history",
            "error_events",
        )
        for key in list_keys:
            old_items = old_trace.get(key)
            new_items = new_trace.get(key)
            merged_trace[key] = (
                list(old_items) if isinstance(old_items, list) else []
            ) + (
                list(new_items) if isinstance(new_items, list) else []
            )

        old_round_count = len(old_trace.get("llm_rounds") or [])
        for idx, item in enumerate(merged_trace["llm_rounds"], start=1):
            if isinstance(item, dict):
                item["round"] = idx
        for item in merged_trace["tool_calls"][len(old_trace.get("tool_calls") or []):]:
            if isinstance(item, dict) and isinstance(item.get("round"), int):
                item["round"] += old_round_count

        total_input = sum(
            int((trace.get("token_cache") or {}).get("input_tokens") or 0)
            for trace in (old_trace, new_trace)
        )
        total_cached = sum(
            int((trace.get("token_cache") or {}).get("cached_tokens") or 0)
            for trace in (old_trace, new_trace)
        )
        merged_trace["token_cache"] = {
            "input_tokens": total_input,
            "cached_tokens": total_cached,
            "cache_ratio": round(total_cached / total_input, 6) if total_input else 0.0,
            "rounds": len(merged_trace["llm_rounds"]),
        }
        merged_trace["total_rounds"] = len(merged_trace["llm_rounds"])
        merged_trace["total_tool_calls"] = len(merged_trace["tool_calls"])
        merged_trace["total_lifecycle_events"] = len(merged_trace["lifecycle_events"])
        merged_trace["total_ms"] = int(old_trace.get("total_ms") or 0) + int(new_trace.get("total_ms") or 0)
        req["execution_trace"] = merged_trace
        return req

    @staticmethod
    def _compact_client_request_history_images(request_history):
        """Keep only the two newest tool-produced images across accumulated LLM payloads."""
        if not isinstance(request_history, list):
            return request_history
        kept_images = 0
        for payload in reversed(request_history):
            messages = payload.get("messages") if isinstance(payload, dict) else None
            if not isinstance(messages, list):
                continue
            for message in reversed(messages):
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if isinstance(content, str):
                    content = ClientOAAgent._normalize_message_image_content(content)
                    message["content"] = content
                if not ClientOAAgent._is_client_tool_image_message(message):
                    continue
                if not isinstance(content, list):
                    continue
                new_parts = []
                dropped = False
                for part in reversed(content):
                    # 与 ClientOAAgent._apply_client_screenshot_window 保持一致：
                    # 空壳（url 为空）无条件降级；真实 base64 图才参与窗口计数。
                    if not isinstance(part, dict) or part.get("type") != "image_url":
                        new_parts.append(part)
                        continue
                    url = (part.get("image_url") or {}).get("url")
                    if not url:
                        # 空壳：无条件降级
                        dropped = True
                        continue
                    if kept_images < ClientOAAgent._SCREENSHOT_WINDOW:
                        kept_images += 1
                        new_parts.append(part)
                    else:
                        dropped = True
                new_parts.reverse()
                if dropped:
                    text_part = next(
                        (
                            part for part in new_parts
                            if isinstance(part, dict) and part.get("type") == "text"
                        ),
                        None,
                    )
                    if text_part is not None:
                        text_part["text"] = str(text_part.get("text") or "") + "\n[截图已省略]"
                    else:
                        new_parts.insert(0, {"type": "text", "text": "[截图已省略]"})
                    message["content"] = new_parts
        return request_history

    @staticmethod
    def _shrink_request_history_images(request_history):
        """缩减 _llm_request_history 中 image_url 的 data:base64 日志数据。

        输入框多模态文件（文档转图片）会为每一页生成一张 base64 图片，工具也会产生
        截图，这些 data URI 逐轮累积会显著放大日志体积。缩减后仅保留 ``data:<mime>;base64,``
        前缀，既能让排障方确认该 message 携带了图片，又不保留实际像素数据。

        幂等：缩减后的 URL 仍以 ``data:`` 开头，且 ``split(",")[0]`` 前后不变，
        重复调用不会继续膨胀。
        """
        if not isinstance(request_history, list):
            return request_history
        for payload in request_history:
            if not isinstance(payload, dict):
                continue
            messages = payload.get("messages")
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if isinstance(content, str):
                    content = ClientOAAgent._normalize_message_image_content(content)
                    message["content"] = content
                if not isinstance(content, list):
                    continue
                for part in content:
                    if not isinstance(part, dict) or part.get("type") != "image_url":
                        continue
                    image_url = part.get("image_url")
                    if not isinstance(image_url, dict):
                        continue
                    url = image_url.get("url")
                    if not isinstance(url, str) or not url.startswith("data:"):
                        continue
                    header = url.split(",", 1)[0]
                    image_url["url"] = f"{header},<base64已省略>"
        return request_history

    def _merge_client_resume_diagnostics_from_local(self, req):
        if not (
            req.get("_client_tool_resume")
            or ClientOAAgent._is_client_tool_resume(req)
        ):
            return req
        file_path = self._build_chatlog_file_path(req)
        if not os.path.isfile(file_path):
            return req
        try:
            with open(file_path, encoding="utf-8") as f:
                previous = json.load(f)
            return self._merge_client_resume_diagnostics(req, previous)
        except Exception:
            logger.warning(
                "客户端续跑诊断历史本地合并失败 | answerId=%s",
                req.get("answerId", ""),
                exc_info=True,
            )
            return req

    @staticmethod
    def _attach_exception_details(req, exc):
        req["exception"] = str(exc)
        req["exception_type"] = type(exc).__name__
        req["exception_traceback"] = traceback.format_exc()
        return req

    @staticmethod
    def _reference_corpus_meta_missing(item):
        if not isinstance(item, dict) or item.get("dataType") != "corpus":
            return False
        corpus = item.get("corpus") or {}
        if not isinstance(corpus, dict):
            return False
        for key in ("createrUserId", "createrUserName", "createrTime", "modifiedTime", "source"):
            if corpus.get(key) in (None, ""):
                return True
        return False

    def _fetch_corpus_info_map(self, tenant_id, corp_file_ids):
        fetch_fn = getattr(self, "get_corpus_info_fn", None)
        if fetch_fn is None or not tenant_id or not corp_file_ids:
            return {}

        body = {"tenantId": tenant_id, "corpFileIds": corp_file_ids}
        try:
            resp = fetch_fn(**body)
        except Exception:
            logger.warning("saveReferenceData corpus compensation request failed | body=%s", body, exc_info=True)
            return {}

        if not isinstance(resp, dict) or resp.get("code") != "0000":
            logger.warning(
                "saveReferenceData corpus compensation non-success | body=%s resp=%s",
                body,
                beautify(resp),
            )
            return {}

        data = resp.get("data") or []
        if not isinstance(data, list):
            return {}
        return {
            str(item.get("id")): item
            for item in data
            if isinstance(item, dict) and item.get("id")
        }

    def _compensate_missing_reference_corpus_meta(self, req, references):
        if not isinstance(references, list) or not references:
            return

        tenant_id = req.get("tenantId")
        missing_doc_ids = []
        for item in references:
            if not self._reference_corpus_meta_missing(item):
                continue
            corpus = item.get("corpus") or {}
            doc_id = corpus.get("corpusId")
            if doc_id:
                missing_doc_ids.append(str(doc_id))

        missing_doc_ids = list(dict.fromkeys(missing_doc_ids))
        if not missing_doc_ids:
            return

        info_map = self._fetch_corpus_info_map(tenant_id, missing_doc_ids)
        if not info_map:
            return

        for item in references:
            if not self._reference_corpus_meta_missing(item):
                continue
            corpus = item.get("corpus") or {}
            doc_id = str(corpus.get("corpusId") or "")
            info = info_map.get(doc_id)
            if not info:
                continue

            for key in ("createrUserId", "createrUserName", "createrTime", "modifiedTime", "source"):
                if info.get(key) not in (None, ""):
                    corpus[key] = info.get(key)
            if info.get("allowDownload") not in (None, "") and corpus.get("allowDownload") in (None, ""):
                corpus["allowDownload"] = info.get("allowDownload")
            if info.get("file_url") not in (None, "") and corpus.get("file_url") in (None, ""):
                corpus["file_url"] = info.get("file_url")
            if info.get("type") not in (None, "") and corpus.get("type") in (None, ""):
                corpus["type"] = info.get("type")
            # 额外文件字段（systemId / fileInfoTag / ecSkipUrl / ecMobileSkipUrl）有值才写入
            merge_corpus_extra_file_fields(corpus, info)

    def saveReferenceData(self, req, cur_step):
        sim_ref_doc = ""
        add_reference_prompt_enabled = req.get("curWriterServiceInfo", {}).get("add_reference_prompt", True)
        if ("cur_ref" in cur_step and "reference_data" in req
                or (
                        cur_step.get("status", None) == "1"
                        and self.reference_documents is not None
                        and req.get("traceability_info")
                        and add_reference_prompt_enabled
                )):
            current_reference_indexs = cur_step.get("cur_ref", []) or []
            reference_indexs = list(dict.fromkeys((req.get("reference_indexs", []) or []) + current_reference_indexs))
            dataList = req.get("reference_data", [])
            # 构建 id -> ele 映射
            id_to_ele = {}
            for ele in dataList:
                if not isinstance(ele, dict) or "id" not in ele:
                    continue
                id_to_ele[ele["id"]] = ele
                id_to_ele[str(ele["id"])] = ele
            reference_id_map = req.get("reference_id_map", {})

            def _resolve_reference_id(ref_id):
                return reference_id_map.get(ref_id, reference_id_map.get(str(ref_id), ref_id))

            if cur_step.get("status", None) == "1" and self.reference_documents is not None and req.get("traceability_info") and add_reference_prompt_enabled:
                documents_info = req["traceability_info"]
                documents_list = ["".join(doc.get("content")) for doc in documents_info.values()]
                index_list, scores_log = self.reference_documents(req["question"], cur_step["answer"], documents_list)

                # 不要打开以下注释
                # logger.info(f"模型工程兜底引用了==>{index_list}")
                # index_list = list(req["traceability_info"])

                map_list = [int(_resolve_reference_id(idx)) for idx in index_list]
                sim_ref_doc = "".join(f"[](citation:{idx})" for idx in set(map_list) - set(reference_indexs))
                reference_indexs = list(dict.fromkeys(reference_indexs + map_list))

                req["simReferenceDoc"] = map_list
                req["simReferenceScore"] = scores_log

            if not reference_indexs:
                return sim_ref_doc

            # 按 reference_indexs 顺序筛选存在的元素
            req["reference_indexs"] = reference_indexs
            dataListIstrace = [
                id_to_ele[resolved_id]
                for rid in reference_indexs
                for resolved_id in [_resolve_reference_id(rid)]
                if resolved_id in id_to_ele
            ]
            self._compensate_missing_reference_corpus_meta(req, dataListIstrace)
            req["dataListIstrace"] = dataListIstrace
            # logger.info("###########")
            # logger.info(cur_step)
            # logger.info(dataListIstrace)
            if dataListIstrace:
                request_body = {
                    "questionId": req.get("questionId", None),
                    "answerId": req.get("answerId", None),
                    "dataList": dataListIstrace,
                }
                try:
                    # logger.debug(request_body)
                    response = self.saveReferenceData_fn(**request_body)
                    # logger.debug(response)
                    if response.get("code", None) != "0000":
                        raise UtilsExceptionBase(6209, self.error_code[6209] + response.get("msg", ""))
                except (UtilsExceptionBase, UtilsException):
                    raise
                except Exception as ue:
                    raise UtilsExceptionBase(6209, self.error_code[6209] + str(ue))
        return sim_ref_doc

    def base64_answer_remove(self, req):
        # 对于文生图的历史记录，将answer base64暂时删掉
        history = req.get("history", [])
        for ele in history:
            if ele.get("answer", "").startswith("![image]"):
                ele["answer"] = "![image]"

    @staticmethod
    def _client_log_document_id(answer_id):
        """为客户端日志首写生成稳定 _id，避免并发 upsert 插出重复文档。"""
        raw = json.dumps(
            {"type": type(answer_id).__name__, "value": answer_id},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        return "client-log:" + hashlib.sha256(raw).hexdigest()

    def _save_mongo_log(
        self,
        req,
        cur_step=None,
        overwrite=False,
        *,
        log_saved_at=None,
        raise_on_total_failure=False,
    ):
        """写入或覆盖 Mongo 请求日志。

        普通链路保持 insert_one；客户端链路使用 answerId 覆盖，保证同一
        answerId 无论 status=11 还是 status=1，都只保留最新一条日志。
        """
        req = self.req_postprocess(req)
        if cur_step is not None:
            req["last_step"] = cur_step

        if overwrite:
            answer_id = req.get("answerId")
            try:
                collection = self.mongodb_op.check_and_get_collection(
                    self.mongo_collection_name
                )
                # 续跑判定不能只依赖 preprocess 后才设置的 _client_tool_resume 标记：
                # _validate_client_request 在 preprocess 更早阶段就可能抛异常，此时该标记
                # 尚未写入，会漏掉诊断历史 merge。这里用请求本身的 resumeType 兜底判断。
                if req.get("_client_tool_resume") or ClientOAAgent._is_client_tool_resume(req):
                    try:
                        previous = collection.find_one(
                            {"answerId": answer_id},
                            {
                                "_llm_request_history": 1,
                                "_llm_response_stream_history": 1,
                                "_llm_cache_records": 1,
                                "_llm_request_history_compaction": 1,
                                "execution_trace": 1,
                            },
                        )
                        self._merge_client_resume_diagnostics(req, previous)
                    except Exception:
                        logger.warning(
                            "客户端续跑诊断历史 Mongo 合并失败 | answerId=%s",
                            answer_id,
                            exc_info=True,
                        )
                # _log_saved_at 是写入版本号，用于防止迟到的旧快照倒灌覆盖新日志。
                # 断开快照在入队时就固定了该值（见 _save_client_disconnect_log），
                # 正常路径在这里取当前时刻。
                # 版本号只接受内部参数，不能信任 HTTP 请求里的同名字段；否则一个
                # 未来时间戳会让该 answerId 后续日志永久无法覆盖。
                saved_at = time.time() if log_saved_at is None else log_saved_at
                mg_req = copy.deepcopy(req)
                mg_req["_log_saved_at"] = saved_at
                self._compact_mongo_diagnostic_history(mg_req)
                # 只覆盖「不比自己新」的文档。条件与写入是原子的（CAS 语义），
                # 因此迟到的旧快照落在新日志之后时匹配不到，不会倒灌覆盖。
                # 历史文档没有该字段，视为最旧，仍可被正常覆盖。
                result = collection.replace_one(
                    {
                        "answerId": answer_id,
                        "$or": [
                            {"_log_saved_at": {"$lte": saved_at}},
                            {"_log_saved_at": {"$exists": False}},
                        ],
                    },
                    mg_req,
                    upsert=False,
                )
                if getattr(result, "matched_count", None) == 0:
                    # 未匹配只有两种可能，用一次读区分：
                    if collection.find_one({"answerId": answer_id}, {"_id": 1}) is None:
                        # 首写使用 answerId 派生出的稳定 _id 做原子 upsert。两个 worker
                        # 即使同时判断为“不存在”，也只会竞争同一个 _id；较新的版本
                        # 可以覆盖，较旧的版本会因 DuplicateKey 被判定为过期。
                        insert_doc = copy.deepcopy(mg_req)
                        insert_doc["_id"] = self._client_log_document_id(answer_id)
                        try:
                            collection.replace_one(
                                {
                                    "_id": insert_doc["_id"],
                                    "$or": [
                                        {"_log_saved_at": {"$lte": saved_at}},
                                        {"_log_saved_at": {"$exists": False}},
                                    ],
                                },
                                insert_doc,
                                upsert=True,
                            )
                        except DuplicateKeyError:
                            # 竞争者已写入同一稳定 _id（或命中其他唯一约束）。
                            # 重试一次 answerId CAS：本版本更新则覆盖，否则才是
                            # 正常的旧快照丢弃。
                            retry = collection.replace_one(
                                {
                                    "answerId": answer_id,
                                    "$or": [
                                        {"_log_saved_at": {"$lte": saved_at}},
                                        {"_log_saved_at": {"$exists": False}},
                                    ],
                                },
                                mg_req,
                                upsert=False,
                            )
                            if getattr(retry, "matched_count", None) == 0:
                                logger.info(
                                    '答案ID：%s;首写竞态中已有更新日志，丢弃旧快照 | '
                                    '快照版本=%.3f',
                                    answer_id,
                                    saved_at,
                                )
                    else:
                        # 现有文档更新：本快照是迟到的旧版本，丢弃而不是覆盖
                        logger.info(
                            '答案ID：%s;已有更新的日志版本，丢弃迟到的旧快照 | 快照版本=%.3f',
                            answer_id,
                            saved_at,
                        )
            except Exception as mongo_error:
                # MongoDB 写日志失败（连接异常 / DocumentTooLarge 等）不应影响
                # 正常业务返回，降级为日志告警即可；本地文件兜底在下方仍会执行。
                logger.error(
                    "MongoDB 客户端日志写入失败 | answerId=%s",
                    answer_id,
                    exc_info=True,
                )
                file_path = ""
                try:
                    file_path = self.save_log2local_file(req, force=True)
                    logger.warning(
                        "MongoDB 客户端日志已降级保存到本地 | answerId=%s | file=%s",
                        answer_id,
                        file_path,
                    )
                except Exception:
                    logger.error(
                        "MongoDB 客户端日志本地兜底失败 | answerId=%s",
                        answer_id,
                        exc_info=True,
                    )
                if not file_path and raise_on_total_failure:
                    raise RuntimeError(
                        f"客户端日志 Mongo 与本地兜底均写入失败: answerId={answer_id}"
                    ) from mongo_error
                return "local_fallback" if file_path else "failed"
            # Mongo 写入成功时仍遵循 chatlog2localfile 配置，避免生产环境重复落盘。
            try:
                self.save_log2local_file(req)
            except Exception:
                # Mongo 已成功落库，可选的本地副本失败不应被 worker
                # 计为「彻底写入失败」。
                logger.warning(
                    "MongoDB 已写入，可选本地日志落盘失败 | answerId=%s",
                    answer_id,
                    exc_info=True,
                )
            return "mongo"

        mg_req = copy.deepcopy(req)
        self._compact_mongo_diagnostic_history(mg_req)
        local_path = self.save_log2local_file(req)
        try:
            collection = self.mongodb_op.check_and_get_collection(
                self.mongo_collection_name
            )
            collection.insert_one(mg_req)
            return "mongo"
        except Exception as mongo_error:
            logger.error(
                "MongoDB 普通日志写入失败 | answerId=%s",
                req.get("answerId", ""),
                exc_info=True,
            )
            if not local_path:
                try:
                    local_path = self.save_log2local_file(req, force=True)
                except Exception:
                    logger.error(
                        "MongoDB 普通日志本地兜底失败 | answerId=%s",
                        req.get("answerId", ""),
                        exc_info=True,
                    )
            if not local_path and raise_on_total_failure:
                raise RuntimeError(
                    f"普通日志 Mongo 与本地兜底均写入失败: "
                    f"answerId={req.get('answerId', '')}"
                ) from mongo_error
            return "local_fallback" if local_path else "failed"

    def __call__(self, req):
        # 在 try 之前绑定：GeneratorExit 可能发生在 stream_call 之前
        # （如 get_user_mcps 期间），此时 response 尚未赋值。
        response = None
        # 是否已由 status=1 / 11 分支落过库。终态帧 yield 出去之后、循环体恢复之前
        # 客户端若断开，GeneratorExit 会先到，不判重会重复写一条（普通链路是
        # insert_one，会插出重复文档）。
        saved = False
        try:
            if (
                self.mcp_developer_mode
                and not req.get("openClaw")
                and req.get("functionName") == "sceneChat"
                and req.get("channel") != "aiSearch"
            ):
                self.get_user_mcps(req)

            is_client_request = ClientOAAgent._is_client_request(req)
            is_client_tool_resume = ClientOAAgent._is_client_tool_resume(req)
            if is_client_tool_resume:
                logger.info(
                    "Client Stream Resume | answerId=%s | functionName=%s | clientType=%s",
                    req.get("answerId", ""),
                    req.get("functionName", ""),
                    (req.get("algExtra") or {}).get("clientType"),
                )
            # 普通请求
            response = self.stream_call(req)
            last_zero = None
            for res in response:
                # logger.debug(res)
                cur_step = json.loads(re.sub("^data: ", "", res).strip("\n"))
                if cur_step.get("status", None) == "0":
                    last_zero = cur_step
                sim_ref_doc = self.saveReferenceData(req, cur_step)
                if cur_step.get("status", None) == "1":
                    # self.saveReferenceData(req)
                    if sim_ref_doc and last_zero:
                        last_zero["token"] = sim_ref_doc
                        last_zero["seq"] += 1
                        yield f"data: {json.dumps(last_zero, ensure_ascii=False)}\n\n"
                        cur_step["seq"] += 1
                        res = f"data: {json.dumps(cur_step, ensure_ascii=False)}\n\n"
                    logger.debug("Last Stream Step")
                    logger.info(f'答案ID：{req.get("answerId", "")};各模块耗时统计: {beautify(req.get("perf", {}))}')
                    if not self.private:
                        t1 = time.time()
                        self._save_mongo_log(
                            req,
                            cur_step=cur_step,
                            overwrite=is_client_request or is_client_tool_resume,
                        )
                        logger.info(f'答案ID：{req.get("answerId", "")};MongoDB日志写入耗时: {(time.time() - t1):.3f}s')
                    else:
                        self._save_private_diagnostic_doc(req, outcome="success")
                    saved = True
                elif cur_step.get("status", None) == "11":
                    logger.info(
                        "Client Stream Interrupted | answerId=%s | functionName=%s | clientType=%s",
                        req.get("answerId", ""),
                        req.get("functionName", ""),
                        (req.get("algExtra") or {}).get("clientType"),
                    )
                    if not self.private:
                        t1 = time.time()
                        self._save_mongo_log(
                            req,
                            cur_step=cur_step,
                            overwrite=True,
                        )
                        logger.info(
                            '答案ID：%s;客户端中断日志写入耗时: %.3fs',
                            req.get("answerId", ""),
                            time.time() - t1,
                        )
                    else:
                        self._save_private_diagnostic_doc(req, outcome="client_interrupt")
                    saved = True
                yield res
        except GeneratorExit:
            # 客户端断开（前台点「停止」/ 网络断连）：gunicorn 关闭 WSGI 响应迭代器，
            # GeneratorExit 沿 yield 链上抛到这里。该路径既非 status=1（正常结束）
            # 也非 status=11（client 工具中断），原先两个落库分支都进不去，导致
            # 该轮会话前台看得见、后台按 answerId 查不到。
            if not saved:
                # 此刻内层生成器还挂在自己的 yield 上、尚未收尾（沙箱未释放 /
                # execution_trace 未回写），而 for 循环不会因异常自动关闭它。
                # 必须先 close() 让内层 finally 跑完，否则存的是半成品快照。
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        logger.warning(
                            '答案ID：%s;客户端断开后关闭内层生成器异常',
                            req.get("answerId", ""),
                            exc_info=True,
                        )
                self._save_client_disconnect_log(req)
            # 必须继续抛：上层依赖 GeneratorExit 的传播做资源清理。本分支内也不能
            # 再 yield，否则 Python 抛 RuntimeError: generator ignored GeneratorExit。
            raise
        except UtilsExceptionBase as e:
            logger.error(f'答案ID：{req.get("answerId", "")};{str(e)}', exc_info=True)
            req = self._attach_exception_details(req, e)
            req = self.req_postprocess(req)
            if not self.private:
                # 报错也走 _save_mongo_log：客户端链路按 answerId 覆盖，保证同一
                # answerId 只保留最新一条记录，异常轮的 raw_req.history 不再被成功轮挡住。
                self._save_mongo_log(req, overwrite=ClientOAAgent._is_client_request(req))
            else:
                self._save_private_diagnostic_doc(req, outcome="error")
            raise
        except Exception as e:
            logger.error(f'答案ID：{req.get("answerId", "")};{str(e)}', exc_info=True)
            req = self._attach_exception_details(req, e)
            req = self.req_postprocess(req)
            if not self.private:
                self._save_mongo_log(req, overwrite=ClientOAAgent._is_client_request(req))
            else:
                self._save_private_diagnostic_doc(req, outcome="error")
            raise

    def _save_client_disconnect_log(self, req):
        """客户端断开（前台点「停止」/ 网络断连）时的兜底落库。

        与 status=1 / status=11 两条正常落库路径并列。用户在算法回答过程中点
        「停止」，Java 关掉到算法的长连接，算法侧走的是 GeneratorExit，两条正常
        路径都进不去，因此该轮会话一直只有前台可见、后台查不到。这里补一条并打上
        _interrupted 标记，便于与正常轮区分。

        写入交给进程内共享的单线程有界队列（见 _disconnect_log_worker）：客户端已
        断开、无人等待本条日志，不应让 Mongo 延迟占住 gunicorn 请求槽位。
        PyMongo 4.6 虽支持 pymongo.timeout() 单次操作时限，但这里仍用队列将日志
        IO 与断连清理解耦。队列同时限制条数和实际 JSON 字节数；队满或进程被
        max-requests 回收时仍可能丢失这条旁路日志。
        """
        if self.private or self.mongodb_op is None:
            return
        answer_id = req.get("answerId", "")
        try:
            # 版本号必须在快照时刻固定：快照会在队列里排队一段时间才执行，只有拿
            # 「断开发生时刻」才能与后续写入比较先后；版本号作为内部参数传递，
            # 不从请求字段读取。
            saved_at = time.time()
            snapshot = copy.deepcopy(req)
            snapshot["_interrupted"] = {
                "reason": "client_disconnected",
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            # 入队前就做与 Mongo 写入一致的清理/裁剪，否则队列保存的仍是
            # 含完整 base64 的原始快照，12MiB 文档目标无法约束队列内存。
            snapshot = self.req_postprocess(snapshot)
            self._compact_mongo_diagnostic_history(snapshot)
            snapshot_bytes = self._diagnostic_json_size(snapshot)
        except Exception:
            # 本方法由 except GeneratorExit 调用，绝不能抛出别的异常把 GeneratorExit
            # 顶掉，否则上层的清理语义被破坏。
            logger.error(
                '答案ID：%s;客户端断开日志快照失败', answer_id, exc_info=True
            )
            return

        try:
            writer = _get_disconnect_log_writer()
            _enqueue_disconnect_log(
                writer["queue"],
                (self, snapshot, answer_id, saved_at, snapshot_bytes),
                snapshot_bytes,
            )
            _bump_disconnect_log_stat("queued")
        except Full:
            # 队满说明写入侧已经跟不上（多半是 Mongo 阻塞）。丢弃是明确选择：
            # 断开日志是旁路记录，不能为它无限堆积完整请求快照。
            _bump_disconnect_log_stat("dropped")
            logger.warning(
                '答案ID：%s;客户端断开日志队列已满，丢弃本轮记录 | '
                '条数上限=%d | 字节上限=%d | 快照字节=%d | 累计丢弃=%d',
                answer_id,
                _DISCONNECT_LOG_QUEUE_SIZE,
                _DISCONNECT_LOG_QUEUE_MAX_BYTES,
                snapshot_bytes,
                _disconnect_log_stats.get("dropped", 0),
            )
        except Exception:
            logger.error(
                '答案ID：%s;客户端断开日志入队失败', answer_id, exc_info=True
            )

    def record_oa_perf(self, req):
        """记录办公模块耗时信息（优先沿用 req['multichat_receive_time']）"""
        now = time.time()  # 精确、单调递增计时
        perf = req.setdefault("perf", {})  # 建立/获取 perf 子字典

        # ① multichat_receive_time：优先取顶层 req，其次 now
        mcr_time = req.get("multichat_receive_time", now)
        perf["multichat_receive_time"] = mcr_time

        # ② qa_receive_time：本次收到 QA 的时间戳
        perf["qa_receive_time"] = now

        # ③ 计算并记录耗时
        cost = round(now - mcr_time, 4)
        perf["oa_cost_time"] = cost

        # ④ 日志（参数化写法避免无谓拼接）
        logger.info("答案ID：%s; 办公模块耗时: %.2fs", req.get("answerId", ""), cost)

    def stream_call(self, req):
        self.record_oa_perf(req)
        logger.debug(beautify(req))
        req["raw_req"] = copy.deepcopy(req)
        req["raw_functionName"] = req.get("functionName")
        req["private"] = bool(self.private)
        validate_network_config_when_enabled(req)

        if llm_model := req.get("algExtra", {}).get("channelMergeConfig", {}).get("qa", {}).get("llm_model", None):
            req["llm_model"] = llm_model
            req["model_info"] = req.get("algExtra", {}).get("channelMergeConfig", {}).get("qa").get("model_info", {})

        if not self.private and req.get("channel") == "avatar" and req.get("functionName") == "writingPeople" and not self._avatar_agent_enabled(req):
            model_config = load_json("resources/common_data/dicts/tmp_model_config.json").get("avatar_llm_model")
            req["llm_model"] = model_config.get("model")
            req["thinking_status"] = model_config.get("thinking_status")
            self.get_model_info(req)
        req["model_info"] = self.model_info_preprocess(req.get("model_info", {}))
        perf = req.setdefault("perf", {})
        perf["model_info_preprocess_cost_time"] = round(time.time() - perf["qa_receive_time"], 4)

        # logger.debug(beautify(req))
        if "question" not in req:
            req["question"] = "请按照要求回复我"
        if req.get("functionName") == "sceneChat":
            req["functionName"] = "generalChat"
        start_time = time.time()
        logger.info(f"+-" * 35)
        req["start_time"] = start_time
        req["request_time"] = datetime.fromtimestamp(start_time)
        questionId = req.get("questionId", "")
        query = req["question"]

        # 模型能力不行、正则被迫营业
        self.query_regex_rephrase(req)

        if req.get("tenantId") in self.not_encryption_user or self.private == "private":
            req["encryption"] = False

        functionName = req.get("functionName", "")
        knowledge_base = req.get("algExtra", {}).get("algSelfParam", {}).get("knowledge_base")
        force_knowledge_qa = (
            req.get("customerQaParams", False)
            or functionName == "searchDoc"
            or knowledge_base == "E9QA"
            or req.get("mode", "") == "open"
        )
        # logger.debug(req)
        logger.info(f"用户当前输入：{query}")
        # logger.info(f"当前用户ID是：{user_id}")
        logger.info(f"当前用户问题ID是：{questionId}")
        if functionName == "voicePhone":
            yield from self.phone_call(req)
        elif req.get("openClaw"):
            yield from self.openclaw(req)
        elif functionName == "writingBissRuleCheck":
            yield from self.biss_rule_check(req)
        elif functionName == "voiceAgent":
            async_iter = self.voice_agent(req)
            yield from bridge_async_iter(async_iter)
        elif functionName in ("agentChat", "agentDebug"):
            async_iter = self.generic_agent(req)
            yield from bridge_async_iter(async_iter)
        elif req.get("channel", None) == "writer" and functionName == "writingConfig":
            # 对话式自定义Agent
            # configType 对话式创建AI智能体，0-父级智能体，1-子级智能体
            if req.get("curWriterServiceInfo", {}).get("configType", 1) == 1:
                yield from self.qa_build_agent(req)
            else:
                yield from self.qa_build_parent_agent(req)
        elif req.get("channel", None) == "writer" and req.get("curWriterServiceInfo", {}).get("useAllSliceCorpus", 0) and req.get("algExtra", {}).get(
                "fileList", None):
            yield from self.long_doc_agent(req)
        elif req.get("channel", None) == "writer":
            yield from self.writing(req)
        elif req.get("channel", None) == "avatar" and functionName == "writingPeople":
            # 数字分身（此时为数字分身入口提问）
            if self._avatar_agent_enabled(req):
                async_iter = self._select_avatar_agent(req)(req)
                yield from bridge_async_iter(async_iter)
            else:
                yield from self.avatar(req)
        elif functionName == "2nd_dev":
            yield from self.second_dev(req)
        elif functionName == "personRecommend":
            async_iter = self.person_recommend(req)
            yield from bridge_async_iter(async_iter)
        elif ClientOAAgent._is_client_request(req):
            logger.info(
                "route to ClientOAAgent | functionName=%s | raw_functionName=%s | clientType=%s",
                req.get("functionName", ""),
                req.get("raw_functionName", ""),
                (req.get("algExtra") or {}).get("clientType"),
            )
            async_iter = self.client_oa_agent(req)
            yield from bridge_async_iter(async_iter)
        elif req.get("customerQaParams", False) and req.get("algExtra", {}).get("sceneMcp", {}).get("enable") == 1:
            logger.info(
                "route to CustomerQAAgent | functionName=%s | raw_functionName=%s | customerQaParams=%s | sceneMcp.enable=%s",
                req.get("functionName", ""),
                req.get("raw_functionName", ""),
                bool(req.get("customerQaParams", False)),
                req.get("algExtra", {}).get("sceneMcp", {}).get("enable"),
            )
            async_iter = self.customer_qa_agent(req)
            yield from bridge_async_iter(async_iter)
        elif force_knowledge_qa:
            logger.info(
                "skip OAAgent and force knowledge_qa | functionName=%s | customerQaParams=%s | knowledge_base=%s | mode=%s",
                functionName,
                bool(req.get("customerQaParams", False)),
                knowledge_base,
                req.get("mode", ""),
            )
            yield from self.knowledge_qa(req)
        elif req.get("algExtra", {}).get("sceneMcp", {}).get("enable") == 1:
            async_iter = self.oa_agent(req)
            yield from bridge_async_iter(async_iter)
        else:
            self.base64_answer_remove(req)
            if self.mcp_developer_mode:
                if req.get("mcp_ids", []):
                    async_iter = self.mcp_agent(req)
                    yield from bridge_async_iter(async_iter)
                else:
                    yield from self.knowledge_qa(req)

            else:
                yield from self.knowledge_qa(req)

    def _avatar_agent_enabled(self, req):
        alg_extra = req.get("algExtra") or {}
        writing_people = alg_extra.get("writingPeople") or {}
        return parse_bool(writing_people.get("avatarAgentEnable"), default=False)

    def _select_avatar_agent(self, req):
        if (req.get("algExtra") or {}).get("emMessgeInfo"):
            return self.avatar_em_agent
        return self.avatar_page_agent


class OAUserChat(BaseChat):
    def __init__(self, xiaoe_chat_config, qa_config, oa_config, private=False) -> None:
        super(OAUserChat, self).__init__(oa_config)
        self.intention_checker_v2 = IntentionCLS(xiaoe_chat_config, qa_config, oa_config, private=private)
        self.intention_chat = IntentionChatV2(xiaoe_chat_config, oa_config, private=private)

        self.private = private
        # self.mongodb_op = None
        # self.mongo_collection_name = None
        # if not private:
        #     from project_utils.db.mongodb_operation import MongoDBOperation
        #     self.mongodb_op = MongoDBOperation(**oa_config["db"]["mongodb"])
        #     self.mongo_collection_name = oa_config["db"]["mongodb"]["collection_name"]

        # self.llm_model = config.get('oa', {}).get('oa_llm_model', 'minimax')

    def stream_call(self, _request):
        _request["start_time"] = time.time()
        questionId = _request.get("questionId", "")
        query = _request["question"]
        user_id = _request.get("userId", "")
        logger.info(f"query：{query}, userID：{user_id}\n questionID：{questionId}\n")
        usage = {
            "llm_model": _request.get("algExtra", {}).get("channelMergeConfig", {}).get("work", {}).get("llm_model", ""),
            "total_tokens": 0
        }
        # if self.is_all_punctuation(query):
        #     yield from self.content_stream(self.punctuation_answer, questionId, usage, function_name="sceneChat", req=_request)

        if self.political_sensitive(query):
            yield from self.content_stream(self.political_answer, questionId, usage, function_name="sceneChat", req=_request)
        else:
            intention_checker = self.intention_checker_v2

            # if self.config["env"] == "dev":
            intention_check_result = intention_checker.intention_cls(_request)
            # else:
            #     intention_check_result = intention_checker.oa_intention(req)

            # 这里把对话状态类型暂存到 req 中, 后续的 multi_chat 根据该值判断 oa/qa, 因为 json 不支持 ConversationStatus 对象序列化, 所以这里取值处理
            _request["convStatus"] = intention_check_result.value
            if intention_check_result not in [ConversationStatus.NONE, ConversationStatus.ERROR]:
                if intention_check_result == ConversationStatus.HAS:  # 有意图，让用户选择意图
                    logger.info(f"意图识别结果：有新意图，让用户选择意图, startSceneList:{beautify(_request.get('startSceneList'))}")

                    yield from self.content_stream("", questionId, usage=usage, extra_content=_request, function_name="sceneChat")
                elif intention_check_result in [ConversationStatus.ON_CUR, ConversationStatus.SWITCH_SAME]:  # 有未处理完的意图，继续处理意图
                    logger.info(f"意图识别结果：继续处理当前意图, 进行表单抽取")
                    response = self.intention_chat.chat(_request)
                    yield from self.content_stream(response.get('errorMsg', ""), questionId, usage=usage, extra_content=response,
                                                   function_name="sceneChat")
                elif intention_check_result == ConversationStatus.SYSTEM_OP:  # 系统操作类对话，如提交表单
                    # 如果是明细表表单新增行的表单操作, 需要调用表单抽取来新增行
                    if _request["identifySessionStep"] == Status.ADD_DETAIL_ROW.value:
                        logger.info(f"新增明细表行的系统操作")
                        response = self.intention_chat.chat(_request)
                        yield from self.content_stream(response.get('errorMsg', ""), questionId, usage=usage, extra_content=response,
                                                       function_name="sceneChat")
                    else:
                        logger.info(f"意图识别结果：系统操作类意图，如提交表单")
                        logger.info(f'意图算法返回结果：{_request}')
                        yield from self.content_stream("", questionId, usage=usage, extra_content=_request, function_name="sceneChat")
                elif intention_check_result == ConversationStatus.CANCEL:  # 未进入意图时收到，如"取消"，的请求
                    logger.info(f"意图识别结果：未进入意图时收到，如'取消'，的请求")
                    # logger.info(f'意图算法返回结果：{req}')
                    yield from self.content_stream("好的。", questionId, usage=usage, function_name="generalChat")
            elif _request.get("qaRecommendWork", 0):
                yield from self.content_stream("", questionId, usage=usage, extra_content=_request,
                                               function_name="sceneChat")
            else:
                allow_channel = _request.get("algExtra", {}).get("channelMergeConfig", {}).get("allowChannel")
                merge = _request.get("algExtra", {}).get("channelMergeConfig", {}).get("merge")
                # 2024-10-12, 测试提出提示词不通用,产品给了新的提示词
                logger.info(f"allowChannel: {allow_channel}")
                # 合并入口且只有办公模块
                if allow_channel == ["work"] and merge == 1:
                    yield from self.content_stream("没有匹配上相关意图，请联系管理员添加。", questionId, usage=usage, function_name="generalChat")
                else:
                    yield from self.content_stream("没有找到相关的功能，您是否想进行知识问答？", questionId, usage=usage, function_name="generalChat")

    def __call__(self, _request):
        yield from self.stream_call(_request)
        # from datetime import datetime, timezone, timedelta
        # try:
        #     pass
        # finally:
        #     if not self.private:
        #         if _request.get("fieldList", None):  # 自用系统日志记录
        #             request_body = {k: v for k, v in _request.items() if k != "algExtra"}
        #             request_body.update({"expires_at": datetime.now(timezone.utc) + timedelta(days=7)})
        #             # request_body.update({"expires_at": datetime.now(timezone.utc) + timedelta(minutes=1)})
        #             self.mongodb_op.insert_one(doc=request_body, collection_name=self.mongo_collection_name)


class OASysChat(BaseChat):
    """
    只需要判断系统内置意图, 不需要返回结果, 因为直接把结果写在 req 里了,
    """

    def __init__(self, xiaoe_chat_config, qa_config, oa_config, private=False) -> None:
        super(OASysChat, self).__init__(oa_config)
        self.intention_checker_v2 = SysIntentionCLS(xiaoe_chat_config, qa_config, oa_config, private=private)
        self.private = private
        self.need_intent = qa_config.get("need_intent", False)

    def __call__(self, req):
        if self.need_intent and not self.private:
            self.intention_checker_v2.intention_cls(req)


class OADirectChat(BaseChat):
    """
    需要判断模型已训练的内置意图, 需要返回结果, 直接是意图名称
    """

    def __init__(self, xiaoe_chat_config, qa_config, oa_config, private=False) -> None:
        super(OADirectChat, self).__init__(oa_config)
        self.direct_intention = DirectIntentionCLS(xiaoe_chat_config, qa_config, oa_config, private=private)
        self.private = private

    def __call__(self, **req):
        return self.direct_intention.intention_cls(**req)


class ConversationType(Enum):
    OA = "oa"
    QA = "qa"
    BOTH = "both"


class MultiChat:
    """
    用于 QAChat 和 OAChat 合并的统一入口
    """

    def __init__(self, multi_chat_config, qa_config, oa_config, xiaoe_chat_config, xiaoe_agent_config, private=False):
        self.multi_chat_config = multi_chat_config
        self.qa_chat = QAChat(xiaoe_chat_config, qa_config, xiaoe_agent_config, private=private)
        # 用于判断用户自定义的意图, 如创建会议室, 请假等
        self.user_oa_chat = OAUserChat(xiaoe_chat_config, qa_config, oa_config, private=private)
        # 用于判断系统内置的意图, 如查文档等, 这个结果需要给到问答去进行检索对应的内容, 如文档类内容
        self.sys_oa_chat = OASysChat(xiaoe_chat_config, qa_config, oa_config, private=private)
        self.question_connection_judge = QuestionConnectionJudge(xiaoe_chat_config, qa_config)

        self.oa_stream_data = None
        self.private = private

        with open("resources/common_data/dicts/sys_intents.json") as f:
            self.sys_intents = json.load(f)
        self.mongodb_op = None
        if not private:
            from project_utils.db.mongodb_operation import MongoDBOperation
            self.mongodb_op = MongoDBOperation(**qa_config["db"]["mongodb"])

    def analyse_oa_stream(self):
        """
        根据 OA 的流数据 获取一些分析必须值,
        e.g. in_scene, intentions, conv_status, fields_is_empty, fields_changed
        (是否在某个意图的会话中, 当前 query 是否有可能的意图, 当前会话状态, 抽取字段是否为空, 抽取的字段是否改变了原表单的字段值)
        当一句话既有意图又要进行表单抽取时, 后端是分两次调用 chat_worker,
        第一次是意图判断, 这时只有 in_scene = False 和 intentions 的值可以获取到
        第二次是 in_scene 的表单抽取, 这时可以获取到所有值
        Args:

        Returns:

        """
        last_stream = copy.deepcopy(self.oa_stream_data)[-1]
        last_stream = json.loads(last_stream.replace("data: ", "").strip())
        # 第一次意图判断和第二次表单抽取都有 in_scene, intentions, first_time_in_scene
        in_scene = last_stream.get("extraData", {}).get("sessionStep") == "inScene"
        intentions = last_stream.get("extraData", {}).get("startSceneList")
        # 检测 in_scene 时是否需要切换意图
        switch_scene = last_stream.get("extraData", {}).get("identifySessionStep") == "switchScene"
        conv_status = last_stream.get("extraData", {}).get("convStatus")
        # 第一次意图判断后立马进行第二次表单抽取的请求, 这个值优先级较高, 只要是 in_scene and first_time_in_scene
        # 就认为是 oa, 防止第一句就抽不到字段而误判成qa
        first_time_in_scene = last_stream.get("extraData", {}).get("algExtra", {}).get("firstTimeInScene")
        # 第二次表单抽取才有fields
        fields_is_empty, fields_changed = True, False
        cur_table_type = last_stream.get("extraData", {}).get("curTableInfo", {}).get("tableType", 0)
        # 主表
        if cur_table_type == 0:
            if fields := last_stream.get("extraData", {}).get("fieldList"):
                for field in fields:
                    # 只要抽取到了一个字段就认为抽取的字段不为空
                    if field.get("fieldValue"):
                        fields_is_empty = False
                    # 只要有一个字段值被修改了就认为表单被修改了
                    if field.get("valueChanged"):
                        fields_changed = True
        # 明细表
        elif cur_table_type == 1:
            cur_table_info = last_stream.get("extraData", {}).get("curTableInfo", {})
            name = cur_table_info.get("detailTableName")
            _id = cur_table_info.get("detailTableId")
            detail_tables = last_stream.get("extraData", {}).get("detailTables", [])
            for dt in detail_tables:
                if dt["detailTableId"] == _id and dt["detailTableName"] == name:
                    details = dt.get("details", [])
                    for d in details:
                        for field in d:
                            # 有多行, 所以只能看值是否会被更改
                            # 只要有一个字段值被修改了就认为表单被修改了
                            if field.get("valueChanged"):
                                fields_changed = True
                                fields_is_empty = False

        return in_scene, first_time_in_scene, conv_status, intentions, switch_scene, fields_is_empty, fields_changed

    @staticmethod
    def conversation_type_by_values(
            in_scene=False, first_time_in_scene=False, conv_status=None,
            intentions=None, switch_scene=False,
            fields_is_empty=True, fields_changed=False) -> ConversationType:
        """
        根据是否处于当前意图的会话, 是否有意图, 字段抽取值是否为空, 字段值是否有更改共同判断当前输入是问答还是办公,或者都有
        Args:
            in_scene: 当前是否处于意图会话内
            first_time_in_scene: 当前是否是第一次进入意图后的表单抽取动作
            conv_status: 当前会话的状态
            intentions: 当前意图
            switch_scene: 当前需要切换的意图
            fields_is_empty: 抽取字段是否为空
            fields_changed: 抽取字段是否回填到了后端

        Returns:

        """
        # OA判断意图过程中, 会判断问答对的命中, 命中了, 就直接走 QA
        # 如果表单抽取到了新字段, 就还是走后面的逻辑到 OA
        if conv_status == ConversationStatus.HIT_QA.value and not fields_changed:
            logger.info("hit QA, redirect to QA workflow")
            return ConversationType.QA

        if not in_scene:
            # 如果不在一个完整的意图会话内, 且当前用户的输入有意图, 那么判断为 OA, 否则是 QA
            logger.info(
                f"query {['without', 'with'][bool(intentions)]} intent, redirect to {['QA', 'OA'][bool(intentions)]} workflow")
            return ConversationType.OA if intentions else ConversationType.QA
        else:
            # 如果是用户自己手动选择的意图,则认为是OA(这里是为了解决后续的逻辑冲突:表单未抽取到查文档新的字段认为是QA)
            # 如果是第一次意图判断完后立马进行的表单抽取的请求(first_time_in_scene), 那么就直接认为是 OA
            # first_time_in_scene 是为了 multi_chat 新增的参数, 但是好像和原版 oa_chat 内的 ON_CUR 状态很像?????
            # 意图内如果是系统类操作(如取消表单, 退出表单, 直接填写表单, 查看表单), 则认为是 OA
            # 如果是一个意图会话内有相同的意图, 即 ConversationStatus.SWITCH_SAME, 那么也认为是 OA
            if (first_time_in_scene or
                    conv_status in [
                        ConversationStatus.SYSTEM_OP.value,
                        ConversationStatus.CANCEL.value,
                        ConversationStatus.SWITCH_SAME.value,
                    ] or
                    switch_scene
            ):
                logger.info(f"complex situation, redirect to OA workflow")
                return ConversationType.OA
            # 如果在一个完整的意图会话内, 用户继续输入内容
            # 1.如果抽取的字段值为空, 大概率是 QA
            if fields_is_empty:
                # 1. 用户输入了无关的内容
                # 2.
                logger.info(f"form extraction with no filed value, redirect to QA workflow")
                return ConversationType.QA
            # 2.如果抽取的字段值不为空,
            else:
                # 且字段值发生更改, 则说明还是在 OA 里进行操作
                if fields_changed:
                    logger.info(f"form extraction with new filed value, redirect to OA workflow")
                    return ConversationType.OA
                else:
                    # 字段值未发生更改, 说明两种情况,
                    # 1.抽取了历史字段(用户说了无关字段的信息)
                    # 2.抽取了新的重复字段(用户又说了相同的结果)
                    # TODO 这里两种情况不太好判断, 未来交给大模型去判断, 现简单认为是QA
                    logger.info(f"complex situation, redirect to QA workflow")
                    return ConversationType.QA

    def __call__(self, _request: dict, **kwargs):
        # 这里只存请求数据, 后面的一些响应数据在各自的 chat 里会存
        # self.mongodb_op.insert_one(doc=copy.deepcopy(request_data), collection_name=self.mongo_collection_name)
        # 涉及到 work 模块时, 必须先构造 self.oa_stream_data, 防止没有数据流导致值为 None 后面报错
        # 检测入口是否合并, 且检测模块是否可用,
        # 1.记录请求信息并初始化数据
        # 记录当前时间作为接收请求的时间。
        # 记录接收到的后端请求信息，包含问题、问题 ID 和答案 ID。
        # 从请求数据里获取允许的通道列表。
        # 初始化 query_intentions、query_intentions_cls 和 multichat_receive_time 字段。
        logger.info(
            f"received backend request, multichat request begin"
            f"question => {_request.get('question')},"
            f"questionID => {_request.get('questionId')}, "
            f"answerID => {_request.get('answerId')}"
        )
        _request["query_intentions"] = {_["sceneNameAKA"]: False for _ in self.sys_intents}
        _request["query_intentions_cls"] = False
        _request['multichat_receive_time'] = time.time()

        # openClaw 请求直接走 openclaw 逻辑，跳过 OA/QA 路由
        if _request.get("openClaw"):
            logger.info("openClaw field detected, redirect to OpenClaw workflow")
            yield from self.qa_chat.openclaw(_request)
            return

        # 2.依据允许的通道和请求数据判断对话类型。
        conv_t = self.conversation_type(_request)

        if conv_t == ConversationType.OA:
            """
            若对话类型为办公（OA），遍历 oa_stream_data 并以生成器的形式返回每个流数据。
            """
            # 构造oa流式返回
            for stream in self.oa_stream_data:
                yield stream

        elif conv_t == ConversationType.QA:
            """
            若对话类型为问答（QA）：

            复制请求数据，更新 functionName 字段。普通 QA 转发会把 channel 归一为 qa，
            sceneMcp 入口需要保留 Java 端传入的原始 channel。
            调用 oa_result2markdown 方法把上一轮 OA 搜索结果转换为 Markdown 格式，若有结果则添加到历史记录中。
            调用 qa_chat 方法处理问答请求，并以生成器的形式返回响应流。
            """

            # 构造qa流式返回
            qa_request_data = copy.deepcopy(_request)
            qa_request_data["functionName"] = _request.get("functionName")
            scene_mcp_enable = _request.get("algExtra", {}).get("sceneMcp", {}).get("enable") == 1
            if not scene_mcp_enable:
                qa_request_data["channel"] = "qa"
            # 如果上一轮会话有 oa 的搜索结果, 需要把搜索结果拼接到 query 然后转发给 qa, 从而实现 oa 和 qa 间的多轮会话
            if not scene_mcp_enable and (res_markdown := oa_result2markdown(qa_request_data)):
                bef_scene_name = ""
                if bef_r := qa_request_data.get("befResult", []):
                    bef_scene_name = bef_r[-1].get("scene", {}).get("sceneInfo", {}).get("sceneName", "")
                qa_request_data.get("history", []).append(
                    {
                        "id": "-1",
                        "question": bef_scene_name,
                        "answer": res_markdown,
                        "functionName": "generalChat",
                        "askTime": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    },
                )
                # logger.info(qa_request_data.get("history", []))
            yield from self.qa_chat(qa_request_data)
            # 线上测试意图准确率就直接出结果了, 就不走具体 qa 流程
            # yield "data: " + json.dumps({"q_type":"qa","token":"qa","extraData":{"functionName":"generalChat"}}, ensure_ascii=False) + "\n\n"
        elif conv_t == ConversationType.BOTH:
            # 后端还没支持这种方式
            pass
            # # 构造两种流式返回, 先返回oa(内容较少)
            # # 需要把 oa 返回的流里的最后一个状态值重置为 0, 方便后续传其他的流(后端继续接收第二种流)
            # for stream in self.oa_stream_data:
            #     stream = stream.strip().replace("data: ", "")
            #     stream = json.loads(stream)
            #     if stream["status"] == "1":
            #         stream["status"] = "0"
            #     stream = "data: " + json.dumps(stream, ensure_ascii=False) + "\n\n"
            #     yield stream
            # # 构造qa流式返回
            # qa_request_data = copy.deepcopy(request_data)
            # qa_request_data.update({"channel": "qa", "functionName": request_data.get("functionName")})
            # # 如果上一轮会话有 oa 的搜索结果, 需要把搜索结果拼接到 query 然后转发给 qa, 从而实现 oa 和 qa 间的多轮会话
            # if res_markdown := oa_result2markdown(qa_request_data):
            #     bef_scene_name = ""
            #     if bef_r := qa_request_data.get("befResult", []):
            #         bef_scene_name = bef_r[-1].get("sceneInfo", {}).get("sceneName", "")
            #     qa_request_data.get("history", []).append(
            #         {
            #             "id": "-1",
            #             "question": bef_scene_name,
            #             "answer": res_markdown,
            #             "functionName": "generalChat"
            #         },
            #     )
            #     # logger.info(qa_request_data.get("history", []))
            # yield from self.qa_chat(qa_request_data)
        else:
            raise NotImplementedError("当前对话类型不支持")

    def conversation_type(self, _request):
        allow_channel = _request.get("algExtra", {}).get("channelMergeConfig", {}).get("allowChannel")
        scene_mcp_enable = _request.get("algExtra", {}).get("sceneMcp", {}).get("enable")

        if scene_mcp_enable == 1:
            logger.info("sceneMcp enable=1, skip OA workflow and redirect to QA workflow")
            return ConversationType.QA

        # 这里如果是只走推荐, qaRecommendWork=1, 这里的allow_channel也是["work"]
        if allow_channel == ["work"]:
            logger.info("module only contains work, redirect to OA workflow")
            self.oa_stream_data = list(self.user_oa_chat(copy.deepcopy(_request)))  # 意图识别表单抽取
            return ConversationType.OA

        if allow_channel == ["qa"]:
            # 判断系统内置意图
            # 20250703说是耗时太久了就先去掉
            # oa_request_data = copy.deepcopy(request_data)
            # self.sys_oa_chat(oa_request_data)
            # 这里因为 copy 的关系, 需要把意图的判断结果赋值给原请求参数,
            # if qi := oa_request_data.get("query_intentions"):
            #     request_data["query_intentions"] = qi
            #     request_data["query_intentions_cls"] = True
            # print("query_intentions", request_data.get("query_intentions"))
            # print("query_intentions_cls", request_data.get("query_intentions_cls"))
            logger.info("module only contains qa, redirect to QA workflow")
            return ConversationType.QA

        # first_time_in_scene_type: 当前是否是第一次进入意图的类型
        #                            0: 用户手动选择首次进入意图(手动选择推荐/切换/引导页)
        #                            1: 算法识别出单个意图(后端选择推荐/切换)
        #                            None: 其他
        # 打电话功能直接传给问答处理
        if _request.get("functionName") == "voicePhone":
            return ConversationType.QA
        # 只要不是sceneChat, 就一律认为是 QA, 不管是否 in_scene
        if _request.get("functionName") != "sceneChat":
            logger.info("not a scene chat, redirect to QA workflow")
            return ConversationType.QA
        if _request.get("algExtra", {}).get("firstTimeInSceneType") == 0:
            logger.info("user is firstly in scene, redirect to OA workflow")
            self.oa_stream_data = list(self.user_oa_chat(copy.deepcopy(_request)))
            return ConversationType.OA
        # 首次进入意图。true: 首次进入、false | null | 其他: 非首次进入
        # 1、用户手动选择推荐的意图
        # 2、用户手动选择切换的意图
        # 3、算法识别出单个意图，后端二次交互
        # 4、算法识别出切换单个意图，后端二次交互
        # 5、用户点击引导页意图列表，直接进入意图
        first_time_in_scene = _request.get("algExtra", {}).get("firstTimeInScene")
        if first_time_in_scene:
            self.oa_stream_data = list(self.user_oa_chat(copy.deepcopy(_request)))
            # 一些特殊情况需要直接进问答
            extra_data = json.loads(re.sub("data: ", "", self.oa_stream_data[0])).get("extraData", {})
            if extra_data.get("sceneName", "") in ["查文档", "查询公司相关的资质"]:
                field_list = extra_data.get("fieldList", [])
                empty = True
                for field in field_list:
                    # 当是查文档时, 如果抽取不到文档关键词, 就直接走 QA
                    if field["fieldValue"]:
                        empty = False
                        # 当是查询公司相关的资质时, 如果抽取不到资质关键词或者直接把原句放进去了, 就直接走 QA
                        if field["fieldValue"][0] == _request.get("question", ""):
                            empty = True
                if empty:
                    _request["oaExtra"] = {"forceEndOldScene": True}
                    logger.info("firstly in scene but search documents without keywords, redirect to QA workflow")
                    return ConversationType.QA
            logger.info("firstly in scene, redirect to OA workflow")
            return ConversationType.OA
        if _request.get("algExtra", {}).get("fileList", None):
            # 基于文件的问答, 且经过大模型判断认为当前 question 和文档有关联
            # 这里给到参数question_connection_judge,然后在意图内进行判断, 如果意图内直接有召回分数极高的,就走意图,否则走问答
            _request = self.question_connection_judge(_request)
            # if request_data.get("question_connection_judge", "") == "是":
            #     return ConversationType.QA
        # allow_channel == ["qa", "work"]
        # oa的数据流获取前, 对历史数据进行处理, 只保留 sceneChat 的数据, 即只保留 oa 的历史数据
        oa_request_data = copy.deepcopy(_request)
        oa_history = oa_request_data.get("history", [])
        oa_request_data["history"] = [h for h in oa_history if h.get("functionName") == "sceneChat"]
        self.oa_stream_data = list(self.user_oa_chat(oa_request_data))
        # 这里因为 copy 的关系, 需要把意图的判断结果赋值给原请求参数,
        if qi := oa_request_data.get("query_intentions"):
            _request["query_intentions"] = qi
            _request["query_intentions_cls"] = True
        # print("query_intentions", request_data.get("query_intentions"))
        # print("query_intentions_cls", request_data.get("query_intentions_cls"))
        # oa判断意图过程中可能会命中相似问答对, convStatus为对应值, 这时把问答对更新到参数里给后续的 QA 使用
        # 且转发给 QA 进行后续问答
        hit_qa = {k: v for k, v in oa_request_data.items() if k in ["hit_qa", "relative_q", "simqa_cost_time"]}
        _request.update(hit_qa)

        if oa_request_data.get("convStatus") == ConversationStatus.HIT_QA.value:
            return ConversationType.QA
        # 防止用户自己手打查二开
        elif re.search("^@查二开", _request.get("question", "")):
            logger.info("search scend development, redirect to QA workflow")
            return ConversationType.QA
        else:
            return self.conversation_type_by_values(*self.analyse_oa_stream())
