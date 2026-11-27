// The plain-words answer when a feature is not switched on for the organization's country
// (403 JURISDICTION_INACTIVE, DEV-383). Every app's API layer uses it.
export const NOT_SWITCHED_ON =
  "This feature is not yet approved for your organization's country, so it is switched off. Ask your clinic or Zoiko support if you expected to have it.";
export const isNotSwitchedOn = (code: string | undefined) => code === "JURISDICTION_INACTIVE";
