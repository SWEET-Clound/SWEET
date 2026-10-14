def grade_detect(a : int) -> str:
    if 100 >= a > 90:
        return "A"
    if 90 >= a > 60:
        return "B"
    if 60 >= a >= 0:
        return "C"

def test_grade_detect():
    assert grade_detect(90) == "B"
    assert grade_detect(60) == "C"
    assert grade_detect(100) == "A"
    assert grade_detect(0) == "C"
    assert grade_detect(-5) == None
    assert grade_detect(0) == "C"

# 三段式
def test_grade_detect1():
    # Arrange
    score = 60
    # Act
    result = grade_detect(score)
    # Assert
    assert result == "C"

# 单元/集成/端到端测试
# 单元：一个函数/一个节点
# 集成：测试几个部分是否能对接得上 是否是通的
# 端到端：真实输入到真实输出

# mock是用来隔离不可控的边界
