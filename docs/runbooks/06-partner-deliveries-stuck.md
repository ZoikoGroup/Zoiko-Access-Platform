# 06: Partner deliveries stuck

**Signs:** platform console, **Delivery attempts**: dead letters, or many "will be tried again", for
a customer.

## Check
1. `address_not_approved`: Zoiko has not approved that webhook address for that customer
   (DEV-384). Platform console, **Outgoing addresses**.
2. `unsafe_target`: the address resolves to a private network. The customer must fix it.
3. 4xx answers: the customer's receiver refuses our messages (check their signature check).
4. 5xx answers or timeouts: the customer's receiver is down.

## Act
- If it is the customer's real receiver, approve the address. The customer then resends from the
  staff console (Integrations): support cannot resend for them.
- Tell the customer what the delivery attempts show (status only, never message content).

## Done when
New deliveries arrive, and the customer has resent what they need.
