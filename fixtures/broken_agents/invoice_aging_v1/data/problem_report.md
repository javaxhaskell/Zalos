# Problem report — invoice aging agent

When we ran the aging report this morning, our April invoices showed up in the **90+ days** bucket instead of **0-30**. The March ones are showing **60-90 days**. Numbers look wrong across the board — we use this report to decide which invoices to chase, so we need this fixed before the AR review tomorrow.

We also noticed the agent **crashed on some rows** with an error mentioning "day is out of range for month". Eight or so April invoices didn't make it into the output at all (their bucket reads `PARSE_ERROR`).

Can someone look at this please?

— AR clerk
