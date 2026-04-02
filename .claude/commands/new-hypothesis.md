When the user says /new-hypothesis, help them create a research hypothesis with a complete experiment design.

1. **Understand the idea**: Ask what inspired this hypothesis — a paper, an observation, a previous experiment result. Search vault/papers/ and existing reports in vault/projects/{project}/docs/reports/ for related context.

2. **Formulate the hypothesis**: Help articulate it as a testable statement with:
   - Clear independent variable (what you're changing)
   - Clear dependent variable (what you're measuring)
   - Quantitative threshold for validation/rejection

3. **Design the experiment**: Present a concrete experiment plan for user approval:
   - **What to run**: Exact scripts, commands, configs
   - **Data**: Which benchmark, split, sample size
   - **Models**: Which models to test on
   - **Comparisons**: What baselines or conditions to compare
   - **GPU allocation**: Which GPUs, estimated runtime
   - **Code changes needed**: Any new code to write before running
   - **Output**: Where results will be stored, what metrics to collect
   - **Success criteria**: Exact numbers that validate or reject the hypothesis

4. **Wait for user approval** of the experiment design before proceeding.

5. **After approval**, create the hypothesis:
   - Run: python scripts/hypothesis_tracker.py new (fill in all fields)
   - If code changes are needed, implement them
   - Remind user: "Use `/launch-exp {id}` to start the experiment, then `/debrief` to check progress and collect results."
