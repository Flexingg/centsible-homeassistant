# Centsible for Home Assistant

Your household budget in Home Assistant: account balances, net worth, what's left in each envelope, this month's spending, and how many transactions are waiting for review. It talks to your [Centsible](https://github.com/Flexingg/Centsible) bridge the same way the app does, so it works with any Actual Budget server behind Centsible.

## What you get

One device per budget, with these sensors:

| Sensor | State | Attributes |
|---|---|---|
| **⟨Account⟩ balance** (one per open account) | Current balance | `off_budget`, `bank_sync`, `last_bank_sync` |
| **⟨Category⟩ available** (one per visible expense category) | What's left in the envelope this month | `budgeted`, `spent`, `overspent`, `group`, `month` |
| **Net worth** | Sum of every open account, on and off budget | |
| **To budget** | Money not yet assigned this month | `month` |
| **Budgeted / Spent / Income this month** | Month totals (spending is shown as a positive number) | `month` |
| **Available in envelopes** | Sum of every envelope's balance | `month` |
| **Overspent categories** | How many envelopes are below zero | `categories`: name, group, and how far over |
| **Transactions to review** | Your review inbox in the app | `more_than_500` |

Accounts and categories you add later show up on their own. Money sensors are `monetary` with a `total` state class, so they work in statistics, history graphs and energy-style dashboards.

## Install

**HACS:** add this repository as a custom repository (category *Integration*), install **Centsible**, and restart Home Assistant.

**By hand:** copy `custom_components/centsible` into your Home Assistant `config/custom_components/` folder and restart.

## Set up

1. In the Centsible app, open **More → Settings → Household** and add a member called *Home Assistant*. **Viewer** is enough: the integration only reads.
2. Tap **Invite**. Keep the dialog open.
3. In Home Assistant: **Settings → Devices & services → Add integration → Centsible**, then either
   - paste the whole pairing link (scan the QR code with any QR app; it starts with `actualbridge://pair?`). The link carries the bridge address and your Cloudflare Access service token, so there's nothing else to fill in; or
   - type the code shown under the QR code (like `ABCD-EFGH`) and your bridge address (`https://budget-api.example.com`). If the bridge is behind Cloudflare Access, open **Cloudflare Access** and enter the service token too.
4. If that member can open more than one budget, pick one. Add the integration again for each other budget.

Home Assistant then shows up as a device under that member in the app. Deleting the integration signs that device out.

Codes work once, for 10 minutes. If Home Assistant needs to sign in again (the device was removed in the app, or Home Assistant was off for more than 90 days), it asks for a new invite under **Settings → Repairs**.

**Options:** how often to update (60 seconds by default, 15 at the fastest) and the currency shown on money sensors (Actual doesn't store one; it defaults to your Home Assistant currency).

## How live is it?

The integration asks the bridge every minute by default. On each request the bridge pulls the latest changes from your Actual server first, so edits from the app, Actual's web UI and bank sync show up on the next update. To update immediately, call `homeassistant.update_entity` on any Centsible sensor.

## Examples

Warn when an envelope goes negative:

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.household_overspent_categories
    above: 0
actions:
  - action: notify.mobile_app_phone
    data:
      title: Overspent
      message: >
        {{ state_attr('sensor.household_overspent_categories', 'categories')
           | map(attribute='name') | join(', ') }}
```

Nudge someone when the review inbox piles up: trigger on `sensor.household_transactions_to_review` above 20.

## Privacy and security

- The integration pairs as its own device, with its own tokens. Remove it any time from the app (**Settings → Household**), or by deleting the integration.
- Tokens and the Cloudflare Access secret are kept in Home Assistant's config entry storage, like any other integration's credentials. Diagnostics downloads leave them, and the bridge address, out.
- The bridge rotates refresh tokens on every use and signs a device out if an old one is ever reused. Don't copy `.storage/core.config_entries` to a second Home Assistant: whichever refreshes second gets signed out.

## Develop

```sh
pip install -r requirements_test.txt
pytest                                   # unit tests, against a fake bridge
```

End to end, against a real bridge and Actual server from a Centsible checkout:

```sh
cd ../Centsible/bridge && npm ci && npx tsx test/support/e2e-server.ts 8787   # prints {"pairingUri": ...}
CENTSIBLE_E2E_PAIRING_URI='actualbridge://pair?...' pytest tests/test_e2e.py -s
```

CI runs both, plus `hassfest` and the HACS check.

## License

GPL-3.0, like Centsible.
