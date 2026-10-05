# Rewind

Rewind aims to capture failing Python backend requests and reproduce them locally using recorded dependency outcomes.

**Status:** design stage; capture and replay are not implemented yet.

The proposed first release focuses on bounded HTTP capture, strict offline replay, and useful divergence reports. Database/cache adapters and regression-test generation are planned expansions.

See the [project plan](REWIND_PROJECT_PLAN.md) for the MVP scope, architecture, safety requirements, acceptance tests, and delivery milestones.
