import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "community.vrm-battery"

  property var status: ({})
  property double clockNow: Date.now() / 1000
  readonly property string statePath: (Quickshell.env("XDG_STATE_HOME") || (Quickshell.env("HOME") + "/.local/state")) + "/omarchy-vrm-battery/status.json"
  readonly property string bridgeScript: String(Qt.resolvedUrl("scripts/vrm-battery")).replace(/^file:\/\//, "")
  readonly property string connection: String(status.connection || "unconfigured")
  readonly property bool configured: connection !== "unconfigured"
  readonly property var soc: status.soc || ({})
  readonly property var solar: status.solar || ({})
  readonly property var home: status.home || ({})
  readonly property bool hasAllMetrics: metricValue(soc) !== null && metricValue(solar) !== null && metricValue(home) !== null
  readonly property bool hasStaleMetric: metricIsStale(soc) || metricIsStale(solar) || metricIsStale(home)
  readonly property string freshnessSymbol: connection === "offline" || connection === "auth-required" || connection === "error" ? "×" : (hasStaleMetric ? "!" : (!hasAllMetrics ? "?" : "·"))
  readonly property string freshnessLabel: freshnessSymbol === "·" ? "·" + ageLabel(oldestConfirmation()) : (freshnessSymbol === "!" ? "!" + ageLabel(oldestConfirmation()) : freshnessSymbol)
  readonly property color foreground: root.bar ? root.bar.barForeground : Color.foreground
  readonly property string fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
  readonly property color statusColor: freshnessSymbol === "·" ? root.foreground : (freshnessSymbol === "?" ? Color.accent : Color.urgent)
  readonly property string dashboardUrl: String(status.dashboardUrl || "")
  property bool popupOpen: false

  implicitWidth: row.implicitWidth + Style.space(12)
  implicitHeight: barSize
  visible: true

  function metricValue(metric) {
    if (!metric || metric.value === undefined || metric.value === null) return null
    var number = Number(metric.value)
    return isFinite(number) ? number : null
  }

  function metricIsStale(metric) { return metric && metric.validity === "stale" }

  function watts(value) {
    if (value === null) return "—"
    var absolute = Math.abs(value)
    if (absolute < 1000) return Math.round(value) + "W"
    return (Math.round(value / 100) / 10).toLocaleString(Qt.locale(), "f", 1) + "kW"
  }

  function percent(value) { return value === null ? "—" : Math.round(value) + "%" }

  function ageSeconds(metric) {
    if (!metric || !metric.confirmedAt) return null
    return Math.max(0, clockNow - Number(metric.confirmedAt))
  }

  function oldestConfirmation() {
    var ages = [ageSeconds(soc), ageSeconds(solar), ageSeconds(home)].filter(function(age) { return age !== null })
    return ages.length ? Math.max.apply(Math, ages) : null
  }

  function ageLabel(seconds) {
    if (seconds === null) return "—"
    if (seconds < 60) return Math.floor(seconds) + "s"
    return Math.floor(seconds / 60) + "m"
  }

  function metricDetail(label, metric) {
    var value = metricValue(metric)
    var unit = metric && metric.unit === "%" ? percent(value) : watts(value)
    var age = ageSeconds(metric)
    var suffix = age === null ? "nedostupné" : (metric && metric.validity === "stale" ? "zastarané, potvrdené pred " : "potvrdené pred ") + ageLabel(age)
    return label + ": " + unit + " — " + suffix
  }

  function tooltip() {
    if (connection === "unconfigured") return "VRM nie je pripojené. Pozri README pre bezpečný setup."
    var lines = [metricDetail("Batéria", soc), metricDetail("Solár", solar), metricDetail("Dom", home)]
    if (status.error) lines.push(String(status.error))
    return lines.join("\n")
  }

  function reload() { statusFile.reload() }

  Component.onCompleted: {
    statusFile.reload()
    ensureBridge.running = true
  }

  FileView {
    id: statusFile
    path: root.statePath
    watchChanges: true
    printErrors: false
    onLoaded: {
      try {
        var parsed = JSON.parse(text())
        root.status = parsed && typeof parsed === "object" ? parsed : ({})
      } catch (error) {
        root.status = ({ connection: "error", error: "Neplatný stav VRM bridge." })
      }
    }
    onLoadFailed: root.status = ({ connection: "unconfigured" })
  }

  Process {
    id: ensureBridge
    command: ["bash", root.bridgeScript, "ensure"]
  }

  Timer {
    interval: 5000
    running: true
    repeat: true
    onTriggered: root.clockNow = Date.now() / 1000
  }

  Row {
    id: row
    anchors.centerIn: parent
    spacing: Style.space(5)

    Text {
      text: root.configured ? "󰂄" + root.percent(root.metricValue(root.soc)) : "󰂄 VRM"
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.features: { "tnum": 1 }
      textFormat: Text.PlainText
    }
    Text {
      visible: root.configured
      text: "󰖙" + root.watts(root.metricValue(root.solar))
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.features: { "tnum": 1 }
      textFormat: Text.PlainText
    }
    Text {
      visible: root.configured
      text: "󰍛" + root.watts(root.metricValue(root.home))
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.features: { "tnum": 1 }
      textFormat: Text.PlainText
    }
    Text {
      visible: root.configured
      text: root.freshnessLabel
      color: root.statusColor
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.features: { "tnum": 1 }
      textFormat: Text.PlainText
    }
  }

  MouseArea {
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    onClicked: root.popupOpen = !root.popupOpen
    onEntered: if (root.bar) root.bar.showTooltip(root, root.tooltip())
    onExited: if (root.bar) root.bar.hideTooltip(root)
  }

  PopupCard {
    id: popup
    anchorItem: root
    bar: root.bar
    owner: root
    open: root.popupOpen
    contentWidth: popup.fittedContentWidth(Style.space(310))
    contentHeight: popup.fittedContentHeight(details.implicitHeight)

    Column {
      id: details
      anchors.fill: parent
      spacing: Style.space(9)

      Text {
        width: parent.width
        text: root.status.installationName || "Victron VRM"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.subtitle
        font.bold: true
        elide: Text.ElideRight
        textFormat: Text.PlainText
      }
      Text {
        width: parent.width
        text: root.metricDetail("Batéria", root.soc)
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
      }
      Text {
        width: parent.width
        text: root.metricDetail("Solárne panely", root.solar)
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
      }
      Text {
        width: parent.width
        text: root.metricDetail("Sledované záťaže", root.home)
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
      }
      Text {
        width: parent.width
        visible: root.connection !== "live" || root.status.error
        text: root.status.error || (root.connection === "unconfigured" ? "Spusti scripts/vrm-battery configure v priečinku pluginu." : root.connection)
        color: root.statusColor
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
      }
      Button {
        visible: root.dashboardUrl !== ""
        text: "Otvoriť VRM dashboard"
        foreground: root.foreground
        onClicked: Quickshell.execDetached(["xdg-open", root.dashboardUrl])
      }
    }
  }
}
