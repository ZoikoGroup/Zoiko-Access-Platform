# 05: Texts circuit open

**Signs:** Service levels shows the text provider's circuit "open": after several failures in a
row, nothing is sent to the provider for a while (DEV-200).

## Check
1. Is the provider (Twilio) having an outage? Check its status page.
2. Our side: credentials, `TEXT_PROVIDER`, the sender number.
3. Texts to a market are only sent while its signed country pack switches messaging on (DEV-228).
   A lapsed pack stops texts by design: that is not an outage.

## Act
- Wait: the circuit tries again by itself, and texts wait safely in the queue.
- Critical-result texts to doctors also go by email: make sure email works (04).
- If the outage is long and clinical alerts depend on texts, open an incident.

## Done when
The circuit is closed and the text queue is draining.
