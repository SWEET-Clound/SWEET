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

