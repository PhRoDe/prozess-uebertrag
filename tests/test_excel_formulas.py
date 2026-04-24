from app.excel.formulas import cell, sum_range, multi_add, ratio, safe_ref


def test_cell_converts_column_index():
    assert cell(0, 1) == "A1"
    assert cell(2, 5) == "C5"
    assert cell(25, 1) == "Z1"


def test_sum_range_produces_formula():
    assert sum_range(col_idx=2, first_row=3, last_row=8) == "=SUM(C3:C8)"


def test_multi_add_with_subtractions():
    formula = multi_add(col_idx=2, add_rows=[3, 5], subtract_rows=[7])
    assert formula == "=C3+C5-C7"


def test_multi_add_empty_returns_zero():
    assert multi_add(col_idx=2, add_rows=[]) == "=0"


def test_ratio_wraps_with_iferror():
    formula = ratio(numerator_row=10, denominator_row=5, col_idx=2)
    assert formula == '=IFERROR(C10/C5,"")'


def test_safe_ref_returns_zero_on_missing_row():
    assert safe_ref(col_idx=2, row=None) == "0"
    assert safe_ref(col_idx=2, row=5) == "C5"
