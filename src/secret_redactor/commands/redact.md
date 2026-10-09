---
description: Turn secret masking off or on for this session (secret-redactor)
argument-hint: off | on | status
disable-model-invocation: true
---
The user typed `/redact $ARGUMENTS`. The secret-redactor hook normally handles
this command before it reaches you. If you can read this, that hook is not
installed or failed, so secret masking was NOT changed. Tell the user that, and
do nothing else.
