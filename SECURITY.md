# Security Policy

## Supported versions

| Version | Security fixes |
|---------|----------------|
| Latest release ([Releases](https://github.com/v1k70rk4/ha-petkit-ble/releases/latest)) | Yes |
| Older versions | No, please update first |

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Report it privately instead:

**[Report a vulnerability](https://github.com/v1k70rk4/ha-petkit-ble/security/advisories/new)** (the *Security* tab of this repository, *Report a vulnerability*).

Only you and the maintainer see the report. It helps to include:

- the version of the integration and of Home Assistant,
- the fountain model and firmware,
- what someone could do with it, and under which conditions,
- the steps to reproduce it.

You get an answer within a few days. Once it is fixed, the release notes mention it and thank you by name, unless you would rather not be named. Where it is warranted, a GitHub security advisory (and a CVE) is published with the fix.

## What counts

Anything that lets someone do more than the person who set up Home Assistant intended, for example:

- what the integration does with the **data a fountain, or something pretending to be one, sends over Bluetooth**,
- the **commands** it sends to the fountain, and the entities and services that trigger them,
- the **stored settings** of a config entry.

Not in scope: problems in Home Assistant, its Bluetooth stack, BlueZ or the fountain's own firmware; something that needs an administrator account in Home Assistant already.

## What the fountain itself does not protect

These come from the device, and the integration cannot change them:

- **The Bluetooth link is not paired and not encrypted.** The "secret" sent in the handshake is derived by a fixed, public rule from an id that the fountain itself reports. It is not a credential. Anyone within Bluetooth range can read the fountain's state and change its settings whenever nothing else holds its single connection. While Home Assistant is connected, that slot is taken.
- **What the fountain sends is not authenticated either.** A device that imitates a fountain can feed the integration false readings. The integration treats this data as untrusted: it checks frames and lengths and never acts on it beyond showing it.

Treat the fountain like any other unauthenticated Bluetooth device: do not base anything safety-relevant on its readings.
