from clients.scorer import _build_output


def test_lead_score_is_sum():
    data = {
        "industry_fit": 20,
        "company_size_fit": 18,
        "decision_maker_seniority": 15,
        "budget_likelihood_score": 15,
        "growth_signals": 10,
        "lead_score": 999,
    }
    output = _build_output(data)
    assert output.lead_score == 78


def test_lead_score_capped_at_100():
    data = {
        "industry_fit": 20,
        "company_size_fit": 25,
        "decision_maker_seniority": 20,
        "budget_likelihood_score": 20,
        "growth_signals": 15,
        "lead_score": 999,
    }
    output = _build_output(data)
    assert output.lead_score == 100


def test_budget_likelihood_derived():
    output_high = _build_output({"budget_likelihood_score": 15, "budget_likelihood": "garbage"})
    assert output_high.budget_likelihood == "high"

    output_medium = _build_output({"budget_likelihood_score": 8, "budget_likelihood": "garbage"})
    assert output_medium.budget_likelihood == "medium"

    output_low = _build_output({"budget_likelihood_score": 5, "budget_likelihood": "garbage"})
    assert output_low.budget_likelihood == "low"
