from xautoposter.text_analysis import classify_reaction


def test_agreement_is_not_misclassified_as_disagreement():
    assert classify_reaction("この意見に同意できます。")[0] == "同意"


def test_explicit_disagreement_is_detected():
    assert classify_reaction("この意見には同意できません。")[0] == "反論"


def test_negated_disagreement_with_agreement_is_agreement():
    assert classify_reaction("反対ではありません。賛成です。")[0] == "同意"


def test_negated_disagreement_without_positive_cue_stays_conservative():
    assert classify_reaction("反対ではありません。")[0] == "未分類"
