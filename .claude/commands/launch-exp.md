When the user says /launch-exp followed by a hypothesis ID (e.g., /launch-exp h003):

1. Read experiments/hypotheses/{id}.yaml and show a summary.
2. Read experiments/tasks/{id}.yaml and show the SkyPilot config.
3. Confirm with the user: GPU type, estimated cost, expected duration.
4. Run: python scripts/hypothesis_tracker.py launch {id}
5. Remind the user that webhook_listener.py should be running to catch the callback.
