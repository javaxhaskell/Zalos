"""Author upload profiling does not infer workflow contracts."""

from __future__ import annotations

from pathlib import Path

from agentforge.orchestrator.author_contract import profile_tabular_file


def test_profile_tabular_file_reports_shape_without_contract_inference(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    upload = workspace / "uploads" / "processor.csv"
    upload.parent.mkdir(parents=True)
    upload.write_text(
        "payout_id,transaction_id,processor,settlement_batch,gross_amount,fee_amount,net_amount,status\n"
        "PAY-1,TXN-1,Stripe,BATCH-1,100,2.9,97.1,settled\n"
        "PAY-1,TXN-2,Stripe,BATCH-1,50,1.45,48.55,pending\n",
        encoding="utf-8",
    )

    profile = profile_tabular_file(
        workspace=workspace,
        upload_path=upload,
        upload_format="csv",
    )

    assert profile.input_format == "csv"
    assert profile.agent_input_path == "uploads/processor.csv"
    assert profile.row_count == 2
    assert "transaction_id" in profile.candidate_id_columns
    assert "gross_amount" in profile.candidate_amount_columns
    assert "status" in profile.candidate_status_columns
    assert profile.sample_rows[0]["processor"] == "Stripe"
    assert not hasattr(profile, "calculated_fields")


def test_profile_tabular_file_normalises_xlsx_and_lists_sheets(tmp_path: Path) -> None:
    import openpyxl

    workspace = tmp_path / "ws"
    upload = workspace / "uploads" / "expenses.xlsx"
    upload.parent.mkdir(parents=True)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "expense_export"
    sheet.append(["expense_id", "employee_id", "amount", "approval_status"])
    sheet.append(["EXP-1", "EMP-1", 125.50, "approved"])
    workbook.create_sheet("notes").append(["note"])
    workbook.save(upload)
    workbook.close()

    profile = profile_tabular_file(
        workspace=workspace,
        upload_path=upload,
        upload_format="xlsx",
    )

    assert profile.input_format == "xlsx"
    assert profile.selected_sheet == "expense_export"
    assert profile.agent_input_path == "uploads/normalised_input.csv"
    assert (workspace / "uploads" / "normalised_input.csv").is_file()
    assert profile.plausible_sheets == ["expense_export", "notes"]
    assert profile.workbook_sheets[0].sheet_name == "expense_export"
    assert profile.candidate_amount_columns == ["amount"]
