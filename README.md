# VRM Battery for Omarchy

`community.vrm-battery` is a compact, read-only Omarchy 4 bar widget. It shows
the battery state of charge, total solar production, monitored AC load, and the
age of the oldest displayed value:

```
󰂄78% 󰖙2.4kW 󰍛650W ·5s
```

`·5s` means all three values were confirmed within five seconds. `?` means a
metric is unavailable, `!2m` means at least one displayed value is stale, and
`×` means the live connection is offline. Click the widget for individual
timestamps and a link to the VRM dashboard.

## Install

Clone or publish this repository, then add it with Omarchy:

```bash
omarchy plugin add https://github.com/majur/omarchy-vrm-battery.git --enable
```

For a local checkout, copy it into the user plugin directory or publish it
first. Omarchy only installs plugins from Git URLs and runs their code in the
long-lived shell process; review the repository before enabling it.

## Connect a VRM account

Create a dedicated VRM access token at
<https://vrm.victronenergy.com/access-tokens>. The widget never asks for your
VRM password or reads browser cookies.

Run this command from the installed plugin folder:

```bash
~/.config/omarchy/plugins/community.vrm-battery/scripts/vrm-battery configure
```

The setup asks for the VRM email required by MQTT and a personal access token,
then lists only installations available to that token. The token is stored in
the system Secret Service keyring through `secret-tool`; it is never written to
the plugin configuration, command line, logs, or state file. If the keyring is
locked or unavailable, setup stops instead of falling back to plaintext.
REST discovery rejects all HTTP redirects, so the token is never forwarded to
another origin. The MQTT bridge has no listener and stops automatically shortly
after the widget is disabled or the Omarchy shell exits.

Use a separate monitor-only VRM account if your VRM access model permits it.
The bridge only subscribes to `N/` notifications and sends documented `R/`
keepalive reads. It contains no support for MQTT `W/` control topics.

To remove the local profile and token:

```bash
~/.config/omarchy/plugins/community.vrm-battery/scripts/vrm-battery disconnect
```

Also revoke the token in VRM if it is no longer needed.
If keyring deletion fails, `disconnect` reports the failure and retains the
local profile so that it can be retried; it never claims that the token was
removed when it was not.

The runtime requires Omarchy 4, Python 3, Bash, `secret-tool` and a running
Secret Service keyring (for example GNOME Keyring or KeePassXC). No Python
packages or root privileges are required.

To remove the plugin safely, first disconnect the account, then disable and
remove it:

```bash
scripts/vrm-battery disconnect
omarchy plugin disable community.vrm-battery
omarchy plugin remove community.vrm-battery
```

## Data source and limitations

The bridge uses the official VRM REST API for account and installation discovery
and the VRM MQTT broker over verified TLS for live updates. The bundled
`certs/venus-ca.crt` is the Victron CCGX CA published in the official
`victronenergy/dbus-flashmq` repository; it supplements, rather than replaces,
the system trust store. Its legacy Basic Constraints encoding requires disabling
only OpenSSL's extra strict-extension check; certificate-chain and hostname
verification remain enabled. It listens to the
system service's aggregated battery SoC, DC PV power, and AC consumption paths.
The Solar value is the system-calculated PV aggregate. The Home value is the
AC load monitored by the GX system; it is not necessarily the entire physical
home if the installation lacks meters or has unmonitored DC loads.

The backend sends an initial keepalive and then 30-second read requests for each
displayed system metric, so unchanged readings are re-confirmed. Values become
stale after 90 seconds. A
cloud connection cannot guarantee the time at which a physical device measured
the value, so the UI says “confirmed” rather than claiming exact sensor age.

The GX device must have VRM MQTT forwarding available. If MQTT is disabled,
the widget remains offline and deliberately does not pretend that REST polling
is real-time.

## Development checks

```bash
omarchy plugin validate .
python3 -m py_compile backend/vrm_battery.py
```

No test credentials are included. The protocol paths are based on Victron's
system service and must be compared to the selected installation before a
release is considered production-ready.
