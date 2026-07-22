from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentSpec:
    key: str
    name: str
    description: str
    artifact_type: str
    skills: tuple[tuple[str, str, str, tuple[str, ...], tuple[str, ...]], ...]

    @property
    def skill_ids(self) -> frozenset[str]:
        return frozenset(skill[0] for skill in self.skills)


PROFILE_AGENT = AgentSpec(
    key="profile",
    name="Employee Onboarding Profile Agent",
    description=(
        "Retrieves and normalizes authorized employee profile context, assesses profile "
        "completeness, and identifies employee-specific onboarding constraints."
    ),
    artifact_type="EMPLOYEE_PROFILE_CONTEXT",
    skills=(
        (
            "get_employee_onboarding_profile",
            "Get employee onboarding profile",
            "Return normalized employee, role, organization, skills, experience, and onboarding context.",
            ("profile", "employee", "onboarding", "context"),
            ("Retrieve the onboarding profile for employee emp-123",),
        ),
        (
            "assess_profile_completeness",
            "Assess profile completeness",
            "Identify missing, inconsistent, or low-confidence employee profile information needed for onboarding.",
            ("profile", "completeness", "validation"),
            ("Check whether this employee profile is complete enough to generate a plan",),
        ),
        (
            "identify_onboarding_constraints",
            "Identify onboarding constraints",
            "Identify employee-specific constraints such as availability, location, accessibility, role, and skill gaps.",
            ("profile", "constraints", "personalization"),
            ("Identify profile constraints that affect this onboarding plan",),
        ),
    ),
)

KNOWLEDGE_AGENT = AgentSpec(
    key="knowledge",
    name="Company Onboarding Knowledge Agent",
    description=(
        "Retrieves grounded company onboarding policies, procedures, mandatory training, "
        "role requirements, tools, contacts, and cited evidence from the approved knowledge base."
    ),
    artifact_type="ONBOARDING_KNOWLEDGE_EVIDENCE",
    skills=(
        (
            "search_onboarding_knowledge",
            "Search onboarding knowledge",
            "Retrieve relevant onboarding policies, procedures, requirements, tools, contacts, and cited evidence.",
            ("knowledge", "rag", "policy", "onboarding"),
            ("Find the onboarding requirements for a frontend developer in Engineering",),
        ),
        (
            "answer_onboarding_question",
            "Answer onboarding knowledge question",
            "Answer an onboarding question using only supported company knowledge and citations.",
            ("knowledge", "question-answering", "citations"),
            ("Which security training is mandatory during the first week?",),
        ),
        (
            "get_role_onboarding_requirements",
            "Get role onboarding requirements",
            "Retrieve role-, department-, location-, and employment-type-specific onboarding requirements.",
            ("knowledge", "role", "requirements", "training"),
            ("Get mandatory requirements for a hybrid software engineer",),
        ),
    ),
)

PLANNING_AGENT = AgentSpec(
    key="planning",
    name="Adaptive Onboarding Planning Agent",
    description=(
        "Generates, revises, adapts, and explains personalized onboarding plans using verified "
        "profile context, company knowledge, progress, feedback, and constraints."
    ),
    artifact_type="ONBOARDING_PLAN",
    skills=(
        (
            "generate_onboarding_plan",
            "Generate onboarding plan",
            "Create a new personalized onboarding plan from verified employee and company context.",
            ("planning", "generation", "onboarding"),
            ("Generate a 30-day onboarding plan using the supplied profile and policy evidence",),
        ),
        (
            "revise_onboarding_plan",
            "Revise onboarding plan",
            "Revise an existing plan using explicit HR, manager, or employee feedback and return a change summary.",
            ("planning", "revision", "feedback"),
            ("Revise this plan to add product training and preserve completed tasks",),
        ),
        (
            "adapt_onboarding_plan",
            "Adapt active onboarding plan",
            "Adapt future and active plan items based on progress, blockers, delays, and changed circumstances.",
            ("planning", "adaptation", "progress", "replanning"),
            ("Adapt the plan because environment setup is delayed by five days",),
        ),
        (
            "explain_onboarding_plan",
            "Explain onboarding plan",
            "Explain why a task, milestone, dependency, owner, or deadline exists in an onboarding plan.",
            ("planning", "explanation", "plan"),
            ("Why is the security training scheduled before repository access?",),
        ),
    ),
)

AGENTS: dict[str, AgentSpec] = {
    spec.key: spec for spec in (PROFILE_AGENT, KNOWLEDGE_AGENT, PLANNING_AGENT)
}
