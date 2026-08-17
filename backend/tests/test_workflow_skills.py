from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.graph.workflow_skills import (
    get_or_create_skill,
    render_skill_markdown,
    skill_out,
    update_skill,
)
from app.models.identity import Company, User
from app.models.workflow import WorkflowSkillVersion


def test_render_skill_markdown_uses_structured_sections():
    md = render_skill_markdown(
        "AP",
        "majority_vote",
        {
            "role": "Judge proposal groups.",
            "rules": "Pick the majority equivalent group.",
            "input_context": "Use proposal summaries.",
            "output_format": '{"selected_group": "string"}',
            "failure_handling": "Return no_selection when no majority exists.",
            "retry_policy": "Do not retry.",
            "selection_reason": "Explain the selected majority.",
        },
    )
    assert "# Majority Vote Skill" in md
    assert "## Rules\nPick the majority equivalent group." in md
    assert "## Output Format" in md


def test_default_manager_review_skill_markdown():
    from app.graph.workflow_skills import default_manager_review_skill, render_skill_markdown

    md = render_skill_markdown("AP", "manager_review", default_manager_review_skill("AP"))
    assert "auto-clean" in md.lower() or "Auto-remove" in md
    assert "vendor" in md.lower()
    assert "amount" in md.lower()


def test_workflow_skill_keeps_two_previous_versions():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        company_id = "company-1"
        user_id = "user-1"
        db.add(Company(id=company_id, name="Company"))
        db.add(
            User(
                id=user_id,
                username="skill_user",
                email="skill@example.com",
                display_name="Skill User",
                is_active=True,
                is_verified=True,
            )
        )
        db.commit()

        skill = get_or_create_skill(db, company_id, "AP", "majority_vote")
        for idx in range(3):
            skill = update_skill(
                db,
                skill,
                {
                    "role": f"Role {idx}",
                    "rules": f"Rules {idx}",
                    "input_context": "Inputs",
                    "output_format": "JSON",
                    "failure_handling": "Fail clearly",
                    "retry_policy": "Retry only when useful",
                    "selection_reason": "Explain selection",
                },
                user_id=user_id,
            )

        versions = (
            db.query(WorkflowSkillVersion)
            .filter(WorkflowSkillVersion.skill_id == skill.id)
            .order_by(WorkflowSkillVersion.version.asc())
            .all()
        )
        assert [v.version for v in versions] == [2, 3]
        out = skill_out(skill, db)
        assert len(out["previous_versions"]) == 2
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
