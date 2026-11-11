# Keeping SOC 2 findings out of an assistant's memory

Vendor SOC 2 reports are usually received under NDA. An assistant can know *that* you review vendors, and
*which* vendors, without ever storing *what the reports said*. The `/activity` endpoint supplies the
non-confidential work context (vendor names, counts, dates) live, so nothing confidential needs to be
remembered.

## 1. System-prompt block

```
Vendor SOC 2 review work is confidential (vendor NDAs). Never save to memory, and never repeat in a later
conversation, anything the soc2_review tools return, except vendor names and work-activity counts and dates.
Findings, verdicts, exceptions, quotes, page numbers, auditor names, control details and report contents
stay in the conversation where they came up.

For work context, call get_soc2_work_activity instead of relying on memory. Mention it the way a colleague
would, briefly and at most once per conversation, when it fits or the user brings up work. Don't nag, don't
turn it into a report, and drop it if the user moves on.

If the user asks what a past review found, say you don't keep review details and offer to run
verify_soc2_review again.
```

## 2. Requirement for the memory system

1. **Never-save scope.** Don't persist any content from the `soc2_review` tool, or from any message that
   quotes or paraphrases it: findings, verdicts, exceptions, quotes, page references, auditor or
   subservice-organization names, control IDs tied to a vendor, report dates.
2. **Enforce it where memories are written,** not only in the prompt. A prompt can be ignored; a filter
   can't. Exclude any turn containing a `soc2_review` tool call or result from memory extraction entirely,
   because the assistant's own reply usually restates the findings.
3. **Allowed exception (optional):** a single line naming vendors reviewed, nothing else. The live
   `/activity` endpoint already provides this, so storing nothing is cleaner.
4. **Work context comes from `/activity` at conversation time,** never from memory.
5. **Recall:** answer questions about past results with a fresh tool call, or say the details aren't kept.
6. **Test:** run a verify, end the conversation, confirm no memory entry contains finding text, a verdict
   beside a vendor name, or a page reference; then ask about the review in a new conversation and expect a
   re-run offer, not findings.
