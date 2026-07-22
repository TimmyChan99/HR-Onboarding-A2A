# Complete operation routing

## `GENERATE_PLAN`

1. Validate identifiers, employee ID, callback configuration, role/start-date constraints, and requested plan scope.
2. Dispatch Profile and Knowledge in parallel when both can work from the original request.
3. If role/department is known only after Profile, dispatch Profile first and Knowledge second.
4. Require both tasks to reach `TASK_STATE_COMPLETED` and validate their domain artifacts.
5. Dispatch Planning with `generate_onboarding_plan` and the two verified artifacts.
6. Validate the plan artifact and send one backend callback.

## `REVISE_PLAN`

1. Require an existing plan and explicit feedback/requested changes.
2. Refresh Profile only when employee context changed or is needed by the revision.
3. Refresh Knowledge only when policy/procedure/training evidence changed or is needed.
4. Run independent refreshes in parallel.
5. Dispatch Planning with `revise_onboarding_plan`.
6. Require a version increment and a change summary covering added, removed, modified, and preserved items.
7. Callback once.

## `ANSWER_QUESTION`

1. Classify the question.
2. Use Knowledge for policy/procedure/training/tool/contact/security/compliance questions.
3. Use Profile for personal role/manager/skills/start-date/profile-completeness questions.
4. Use both in parallel for questions combining employee and company context.
5. Use Planning `explain_onboarding_plan` only when an existing plan must be explained.
6. Synthesize a direct answer with citations, assumptions, confidence, and missing information.
7. Callback once.

## `ADAPT_PLAN`

1. Require an active plan and progress, blockers, changed conditions, or an adaptation trigger.
2. Refresh Profile for role, availability, skill-gap, location, work-mode, or employee-constraint changes.
3. Refresh Knowledge for policy, mandatory-requirement, procedure, training, or company-constraint changes.
4. Run independent refreshes in parallel.
5. Dispatch Planning with `adapt_onboarding_plan` and preserve completed history.
6. Require an adaptation summary, rescheduled/added/removed tasks, dependency changes, and unresolved blockers.
7. Callback once.

## Failure rules

- A required upstream task that is `FAILED`, `REJECTED`, `CANCELED`, `AUTH_REQUIRED`, or `INPUT_REQUIRED` blocks dependent calls.
- Optional context may produce `PARTIAL_SUCCESS` only when the result remains safe and useful.
- Validation, authorization, and unsupported-skill errors are not retried.
- Timeouts, rate limits, and temporary 5xx errors may be retried using the configured retry policy and the same idempotency key.
