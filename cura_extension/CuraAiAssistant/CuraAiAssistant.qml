import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

import UM 1.5 as UM
import Cura 1.0 as Cura

UM.Dialog
{
    id: dialog

    title: "AI Assistant"
    width: 840 * screenScaleFactor
    height: 860 * screenScaleFactor
    minimumWidth: 640 * screenScaleFactor
    minimumHeight: 580 * screenScaleFactor
    backgroundColor: UM.Theme.getColor("main_background")
    closeOnAccept: false  // Return in the input field must send, not close the dialog
    modality: Qt.NonModal  // Cura stays usable (select, move, look) while the assistant is open
    property variant catalog: UM.I18nCatalog { name: "cura" }  // Cura.ComboBox looks this up

    property real u: screenScaleFactor
    property int pad: 12 * u
    property bool bothShown: manager.proposal.length > 0 && manager.ranking.length > 0

    // Enter and Send ask in the one shared conversation; the assistant plans what to do. The three
    // quick prompts below the input run exactly one helper (a wrong fit is handed on by it).
    function askNow(helper)
    {
        if (manager.busy) return
        manager.ask(helper === "" ? "auto" : helper, input.text)
        input.text = ""
    }
    property bool optionsOpen: false

    // The options page edits drafts; nothing changes until "Apply".
    property string draftProvider: "claude"
    property string draftModel: ""      // Claude's model alias
    property string draftEffort: ""
    property bool draftAuto: false
    property string draftModelText: ""  // other providers: free model name
    property string draftBaseUrl: ""
    property string draftKeyVariable: ""
    property string draftHost: ""
    property var providerInfo: providerOf(draftProvider)
    property bool isClaude: draftProvider == "claude"
    property bool draftChanged: draftProvider != manager.providerKey
        || draftModel != manager.modelKey || draftEffort != manager.effortKey
        || draftAuto != manager.autoApply
        || draftModelText != manager.settingFor(draftProvider, "model")
        || draftBaseUrl != manager.settingFor(draftProvider, "base_url")
        || draftKeyVariable != manager.settingFor(draftProvider, "key_variable")
        || draftHost != manager.settingFor(draftProvider, "host")

    function providerOf(key)
    {
        return manager.providers[indexOfKey(manager.providers, key)]
    }

    function hasField(name)
    {
        return providerInfo.fields.indexOf(name) >= 0
    }

    // Load the drafts for one provider: its stored (or default) model and addresses.
    function loadProvider(key)
    {
        draftProvider = key
        draftModelText = manager.settingFor(key, "model")
        draftBaseUrl = manager.settingFor(key, "base_url")
        draftKeyVariable = manager.settingFor(key, "key_variable")
        draftHost = manager.settingFor(key, "host")
    }

    function openOptions()
    {
        draftModel = manager.modelKey
        draftEffort = manager.effortKey
        draftAuto = manager.autoApply
        loadProvider(manager.providerKey)
        optionsOpen = true
    }

    function applyOptions()
    {
        manager.saveOptions(draftProvider, draftModel, draftEffort, draftAuto,
            {"model": draftModelText, "base_url": draftBaseUrl,
             "key_variable": draftKeyVariable, "host": draftHost})
        optionsOpen = false
    }

    function indexOfKey(list, key)
    {
        for (var i = 0; i < list.length; i++)
            if (list[i].key == key) return i
        return 0
    }

    function toneColor(tone)
    {
        // setting_validation_ok is a near-transparent tint in the dark theme: unreadable as text
        if (tone == "better") return "#4cc27a"
        if (tone == "worse") return UM.Theme.getColor("warning")
        return UM.Theme.getColor("text_inactive")
    }

    // Rounded button; `active` is a selected tab, `accent` the main action of a section.
    component PillButton: Button
    {
        id: pill
        property bool active: false
        property bool accent: false
        implicitHeight: 34 * screenScaleFactor
        leftPadding: 16 * screenScaleFactor
        rightPadding: 16 * screenScaleFactor
        contentItem: UM.Label
        {
            text: pill.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            color: (pill.active || pill.accent) ? "white" : UM.Theme.getColor("text")
            font: (pill.active || pill.accent) ? UM.Theme.getFont("default_bold") : UM.Theme.getFont("default")
        }
        background: Rectangle
        {
            radius: height / 2
            opacity: pill.enabled ? 1 : 0.4
            color: (pill.active || pill.accent)
                ? (pill.hovered ? UM.Theme.getColor("primary_hover") : UM.Theme.getColor("primary"))
                : (pill.hovered ? UM.Theme.getColor("action_button_hovered") : UM.Theme.getColor("detail_background"))
            border.width: 1
            border.color: (pill.active || pill.accent) ? "transparent" : UM.Theme.getColor("lining")
        }
    }

    // Check box with a visible tick; Cura's default looks the same checked and unchecked here.
    component Check: CheckBox
    {
        id: box
        indicator: Rectangle
        {
            implicitWidth: 20 * screenScaleFactor
            implicitHeight: 20 * screenScaleFactor
            x: box.leftPadding
            y: box.height / 2 - height / 2
            radius: 5 * screenScaleFactor
            color: box.checked ? UM.Theme.getColor("primary") : "transparent"
            border.width: 1
            border.color: box.checked ? UM.Theme.getColor("primary") : UM.Theme.getColor("text_inactive")
            UM.Label { anchors.centerIn: parent; text: "✓"; color: "white"; visible: box.checked; font: UM.Theme.getFont("default_bold") }
        }
        contentItem: UM.Label
        {
            text: box.text
            leftPadding: box.indicator.width + 8 * screenScaleFactor
            verticalAlignment: Text.AlignVCenter
        }
    }

    ColumnLayout
    {
        anchors.fill: parent
        spacing: dialog.pad

        // Ollama: once the installed models are known, an empty model name takes the first one.
        Connections
        {
            target: manager
            function onStateChanged()
            {
                if (dialog.optionsOpen && dialog.draftProvider == "ollama" && dialog.draftModelText == ""
                    && manager.detectedModels.length > 0)
                    dialog.draftModelText = manager.detectedModels[0]
            }
        }

        // ---- header ----
        RowLayout
        {
            Layout.fillWidth: true
            visible: !dialog.optionsOpen
            spacing: dialog.pad

            ColumnLayout
            {
                Layout.fillWidth: true
                spacing: 2 * dialog.u
                UM.Label { text: "AI Assistant"; font: UM.Theme.getFont("large_bold") }
                UM.Label
                {
                    Layout.fillWidth: true
                    text: manager.contextLine
                    color: UM.Theme.getColor("text_inactive")
                    elide: Text.ElideRight
                }
                UM.Label
                {
                    Layout.fillWidth: true
                    text: manager.selectionLine
                    color: UM.Theme.getColor("primary")
                    elide: Text.ElideRight
                }
            }
            // Which model and effort the next request uses; click to change.
            PillButton
            {
                text: "AI: " + manager.modelSummary
                onClicked: dialog.openOptions()
            }
            PillButton
            {
                text: "Options"
                onClicked: dialog.openOptions()
            }
            PillButton
            {
                text: "New chat"
                enabled: !manager.busy
                onClicked: manager.reset()
            }
        }

        // ---- options page: replaces the conversation; changes count after "Apply" ----
        ColumnLayout
        {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: dialog.optionsOpen
            spacing: dialog.pad

            UM.Label { text: "Options"; font: UM.Theme.getFont("large_bold") }
            UM.Label
            {
                Layout.fillWidth: true
                text: "How the assistant thinks. Changes apply to the next request."
                color: UM.Theme.getColor("text_inactive")
            }

            ColumnLayout
            {
                id: optionsColumn
                Layout.fillWidth: true
                spacing: 6 * dialog.u

                UM.Label { text: "AI provider"; font: UM.Theme.getFont("default_bold") }
                Cura.ComboBox
                {
                    Layout.fillWidth: true
                    model: manager.providers
                    textRole: "text"
                    currentIndex: dialog.indexOfKey(manager.providers, dialog.draftProvider)
                    onActivated: dialog.loadProvider(manager.providers[index].key)
                }
                UM.Label
                {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    color: UM.Theme.getColor("text_inactive")
                    text: dialog.providerInfo.note
                }

                // Claude: a fixed list of model aliases and an effort level.
                UM.Label { visible: dialog.isClaude; text: "AI model"; font: UM.Theme.getFont("default_bold") }
                Cura.ComboBox
                {
                    visible: dialog.isClaude
                    Layout.fillWidth: true
                    model: manager.models
                    textRole: "text"
                    currentIndex: dialog.indexOfKey(manager.models, dialog.draftModel)
                    onActivated: dialog.draftModel = manager.models[index].key
                }
                UM.Label { visible: dialog.isClaude; text: "Effort"; font: UM.Theme.getFont("default_bold") }
                Cura.ComboBox
                {
                    visible: dialog.isClaude
                    Layout.fillWidth: true
                    model: manager.efforts
                    textRole: "text"
                    currentIndex: dialog.indexOfKey(manager.efforts, dialog.draftEffort)
                    onActivated: dialog.draftEffort = manager.efforts[index].key
                }
                UM.Label
                {
                    visible: dialog.isClaude
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    color: UM.Theme.getColor("text_inactive")
                    text: manager.effortHintFor(dialog.draftModel)
                }

                // Every other provider: a free model name and its addresses.
                UM.Label
                {
                    visible: !dialog.isClaude
                    text: dialog.providerInfo.needs_model ? "Model name" : "Model name (optional)"
                    font: UM.Theme.getFont("default_bold")
                }
                Cura.TextField
                {
                    visible: !dialog.isClaude
                    Layout.fillWidth: true
                    placeholderText: dialog.providerInfo.model_hint
                    text: dialog.draftModelText
                    onTextEdited: dialog.draftModelText = text
                }
                RowLayout
                {
                    visible: dialog.draftProvider == "ollama"
                    Layout.fillWidth: true
                    spacing: 8 * dialog.u
                    Cura.ComboBox
                    {
                        Layout.fillWidth: true
                        model: manager.detectedModels
                        displayText: manager.detectedModels.length > 0 ? "Installed models..." : "No models detected yet"
                        enabled: manager.detectedModels.length > 0
                        onActivated: dialog.draftModelText = manager.detectedModels[index]
                    }
                    PillButton
                    {
                        text: "Detect models"
                        enabled: !manager.busy
                        onClicked: manager.detectModels(dialog.draftHost)
                    }
                }
                UM.Label { visible: dialog.hasField("base_url"); text: "Server address"; font: UM.Theme.getFont("default_bold") }
                Cura.TextField
                {
                    visible: dialog.hasField("base_url")
                    Layout.fillWidth: true
                    text: dialog.draftBaseUrl
                    onTextEdited: dialog.draftBaseUrl = text
                }
                UM.Label { visible: dialog.hasField("key_variable"); text: "Environment variable holding the API key"; font: UM.Theme.getFont("default_bold") }
                Cura.TextField
                {
                    visible: dialog.hasField("key_variable")
                    Layout.fillWidth: true
                    text: dialog.draftKeyVariable
                    onTextEdited: dialog.draftKeyVariable = text
                }
                UM.Label { visible: dialog.hasField("host"); text: "Server address"; font: UM.Theme.getFont("default_bold") }
                Cura.TextField
                {
                    visible: dialog.hasField("host")
                    Layout.fillWidth: true
                    text: dialog.draftHost
                    onTextEdited: dialog.draftHost = text
                }
                Check
                {
                    text: "Apply suggestions automatically"
                    checked: dialog.draftAuto
                    onToggled: dialog.draftAuto = checked
                }
                UM.Label
                {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    color: UM.Theme.getColor("text_inactive")
                    text: "Off by default. When on, settings are changed and the part is rotated as soon as the assistant has decided. Setting changes can be taken back with \"Undo last change\", rotations with Ctrl+Z."
                }
            }

            UM.Label
            {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("primary")
                font: UM.Theme.getFont("default_bold")
                text: "Next requests will use: " + manager.summaryFor(dialog.draftProvider, dialog.isClaude ? dialog.draftModel : dialog.draftModelText, dialog.draftEffort)
            }

            Item { Layout.fillHeight: true }

            RowLayout
            {
                Layout.fillWidth: true
                spacing: 8 * dialog.u
                Item { Layout.fillWidth: true }
                PillButton
                {
                    text: "Cancel"
                    onClicked: dialog.optionsOpen = false
                }
                PillButton
                {
                    text: "Apply"
                    accent: true
                    enabled: dialog.draftChanged
                    onClicked: dialog.applyOptions()
                }
            }
        }

        // ---- the conversation view (hidden while the options page is open) ----
        ColumnLayout
        {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !dialog.optionsOpen
            spacing: dialog.pad

        // ---- conversation ----
        Item
        {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 150 * dialog.u

            ColumnLayout
            {
                anchors.centerIn: parent
                width: parent.width * 0.92
                spacing: dialog.pad
                visible: manager.chat.length == 0

                UM.Label
                {
                    Layout.alignment: Qt.AlignHCenter
                    text: "What do you want to print?"
                    font: UM.Theme.getFont("large_bold")
                }
                UM.Label
                {
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    color: UM.Theme.getColor("text_inactive")
                    text: "Describe it once, in your own words and press Send: the assistant picks the right helper (print settings, the best orientation, or handling the part and setup) and asks if something is unclear. You can also choose a helper yourself with the buttons below the input."
                }
                Flow
                {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignHCenter
                    spacing: 8 * dialog.u
                    Repeater
                    {
                        model: manager.ideas
                        PillButton
                        {
                            text: modelData.text
                            enabled: !manager.busy
                            onClicked: manager.ask("auto", modelData.text)
                        }
                    }
                }
            }

            ListView
            {
                id: chatView
                anchors.fill: parent
                visible: manager.chat.length > 0
                clip: true
                spacing: 6 * dialog.u
                model: manager.chat
                onCountChanged: positionViewAtEnd()
                ScrollBar.vertical: ScrollBar {}

                delegate: Item
                {
                    id: row
                    property bool mine: modelData.role == "user"
                    property bool note: modelData.role == "note"
                    width: chatView.width - 10 * dialog.u
                    height: bubble.height

                    Text
                    {
                        id: measure
                        visible: false
                        text: modelData.text
                        font.family: UM.Theme.getFont("default").family
                        font.pointSize: UM.Theme.getFont("default").pointSize
                        font.italic: row.note
                    }

                    Rectangle
                    {
                        id: bubble
                        width: Math.min(measure.implicitWidth + 2 * dialog.pad + 12 * dialog.u, row.width * (row.note ? 0.92 : 0.78))
                        height: label.implicitHeight + 2 * dialog.pad
                        radius: 12 * dialog.u
                        anchors.right: row.mine ? parent.right : undefined
                        anchors.left: (!row.mine && !row.note) ? parent.left : undefined
                        anchors.horizontalCenter: row.note ? parent.horizontalCenter : undefined
                        color: row.mine ? UM.Theme.getColor("primary") : (row.note ? "transparent" : UM.Theme.getColor("detail_background"))
                        border.width: row.note ? 1 : 0
                        border.color: UM.Theme.getColor("lining")

                        UM.Label
                        {
                            id: label
                            x: dialog.pad
                            y: dialog.pad
                            width: bubble.width - 2 * dialog.pad
                            wrapMode: Text.WordWrap
                            text: modelData.text
                            color: row.mine ? "white" : UM.Theme.getColor("text")
                            font.italic: row.note
                        }
                    }
                }
            }
        }

        // ---- proposal: print settings ----
        ColumnLayout
        {
            Layout.fillWidth: true
            visible: manager.proposal.length > 0
            spacing: 6 * dialog.u

            UM.Label { text: "Suggested changes"; font: UM.Theme.getFont("default_bold") }

            ListView
            {
                id: proposalView
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(contentHeight, (dialog.bothShown ? 130 : 230) * dialog.u)
                clip: true
                spacing: 6 * dialog.u
                model: manager.proposal
                ScrollBar.vertical: ScrollBar {}

                delegate: Rectangle
                {
                    width: proposalView.width - 10 * dialog.u
                    height: card.implicitHeight + 2 * dialog.pad
                    radius: 10 * dialog.u
                    color: UM.Theme.getColor("detail_background")
                    border.width: 1
                    border.color: modelData.selected ? UM.Theme.getColor("primary") : UM.Theme.getColor("lining")

                    RowLayout
                    {
                        id: card
                        anchors.fill: parent
                        anchors.margins: dialog.pad
                        spacing: dialog.pad

                        Check
                        {
                            checked: modelData.selected
                            onToggled: manager.toggle(index, checked)
                        }
                        ColumnLayout
                        {
                            Layout.fillWidth: true
                            spacing: 2 * dialog.u
                            RowLayout
                            {
                                Layout.fillWidth: true
                                UM.Label { text: modelData.label; font: UM.Theme.getFont("default_bold"); elide: Text.ElideRight; Layout.fillWidth: true }
                                UM.Label { text: modelData.from + "  →  " + modelData.to; color: UM.Theme.getColor("primary"); font: UM.Theme.getFont("default_bold") }
                            }
                            UM.Label
                            {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                text: modelData.reason
                                color: UM.Theme.getColor("text_inactive")
                            }
                            UM.Label
                            {
                                Layout.fillWidth: true
                                visible: modelData.warn != ""
                                wrapMode: Text.WordWrap
                                text: "⚠ " + modelData.warn
                                color: UM.Theme.getColor("warning")
                            }
                        }
                    }
                }
            }

            UM.Label
            {
                Layout.fillWidth: true
                visible: manager.rejected.length > 0
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("text_inactive")
                text: "Not usable (dropped): " + manager.rejected.join("; ")
            }
        }

        // ---- test slice tiles ----
        RowLayout
        {
            Layout.fillWidth: true
            visible: manager.tiles.length > 0
            spacing: dialog.pad

            Repeater
            {
                model: manager.tiles
                Rectangle
                {
                    Layout.fillWidth: true
                    implicitHeight: tile.implicitHeight + 2 * dialog.pad
                    radius: 10 * dialog.u
                    color: UM.Theme.getColor("detail_background")
                    border.width: 1
                    border.color: dialog.toneColor(modelData.tone)

                    ColumnLayout
                    {
                        id: tile
                        anchors.fill: parent
                        anchors.margins: dialog.pad
                        spacing: 2 * dialog.u
                        UM.Label { text: modelData.name; color: UM.Theme.getColor("text_inactive") }
                        UM.Label { text: modelData.after; font: UM.Theme.getFont("large_bold") }
                        UM.Label { text: "was " + modelData.before + (modelData.delta != "" ? "   " + modelData.delta : ""); color: dialog.toneColor(modelData.tone) }
                    }
                }
            }
        }

        RowLayout
        {
            Layout.fillWidth: true
            visible: manager.proposal.length > 0 || manager.canRevert
            spacing: 8 * dialog.u

            PillButton
            {
                text: "Apply selected"
                accent: true
                visible: manager.proposal.length > 0
                enabled: !manager.busy && manager.hasSelectedProposal
                onClicked: manager.applySettings()
            }
            PillButton
            {
                text: "Test slice"
                visible: manager.proposal.length > 0
                enabled: !manager.busy && manager.hasSelectedProposal
                onClicked: manager.checkSettings()
            }
            PillButton
            {
                text: "Undo last change"
                visible: manager.canRevert
                enabled: !manager.busy
                onClicked: manager.revertSettings()
            }
        }

        // ---- part plan: scale, copies, arrangement ----
        ColumnLayout
        {
            Layout.fillWidth: true
            visible: manager.hasPartPlan
            spacing: 6 * dialog.u

            UM.Label { text: "Change to the part"; font: UM.Theme.getFont("default_bold") }
            Rectangle
            {
                Layout.fillWidth: true
                implicitHeight: planLines.implicitHeight + 2 * dialog.pad
                radius: 10 * dialog.u
                color: UM.Theme.getColor("detail_background")
                border.width: 1
                border.color: UM.Theme.getColor("primary")

                ColumnLayout
                {
                    id: planLines
                    anchors.fill: parent
                    anchors.margins: dialog.pad
                    spacing: 2 * dialog.u
                    Repeater
                    {
                        model: manager.partLines
                        UM.Label
                        {
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                            text: modelData
                            color: modelData.charAt(0) == "!" ? UM.Theme.getColor("warning") : UM.Theme.getColor("text")
                        }
                    }
                }
            }
            PillButton
            {
                text: "Apply to part"
                accent: true
                enabled: !manager.busy
                onClicked: manager.applyPartPlan()
            }
        }

        // ---- orientation choice ----
        ColumnLayout
        {
            Layout.fillWidth: true
            visible: manager.ranking.length > 0
            spacing: 6 * dialog.u

            UM.Label { text: "Best orientations"; font: UM.Theme.getFont("default_bold") }

            ListView
            {
                id: rankingView
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(contentHeight, (dialog.bothShown ? 120 : 210) * dialog.u)
                clip: true
                spacing: 6 * dialog.u
                model: manager.ranking
                ScrollBar.vertical: ScrollBar {}

                delegate: Rectangle
                {
                    width: rankingView.width - 10 * dialog.u
                    height: 52 * dialog.u
                    radius: 10 * dialog.u
                    color: UM.Theme.getColor("detail_background")
                    border.width: modelData.chosen ? 2 : 1
                    border.color: modelData.chosen ? UM.Theme.getColor("primary") : UM.Theme.getColor("lining")

                    RowLayout
                    {
                        anchors.fill: parent
                        anchors.margins: 8 * dialog.u
                        spacing: dialog.pad

                        Rectangle
                        {
                            Layout.preferredWidth: 30 * dialog.u
                            Layout.preferredHeight: 30 * dialog.u
                            radius: 15 * dialog.u
                            color: modelData.chosen ? UM.Theme.getColor("primary") : UM.Theme.getColor("lining")
                            UM.Label { anchors.centerIn: parent; text: modelData.rank; color: "white"; font: UM.Theme.getFont("default_bold") }
                        }
                        ColumnLayout
                        {
                            Layout.fillWidth: true
                            spacing: 0
                            UM.Label { text: modelData.title + (modelData.chosen ? "   ✓ chosen" : ""); font: UM.Theme.getFont("default_bold") }
                            UM.Label { text: modelData.detail; color: UM.Theme.getColor("text_inactive"); elide: Text.ElideRight; Layout.fillWidth: true }
                        }
                        Rectangle
                        {
                            Layout.preferredWidth: 90 * dialog.u
                            Layout.preferredHeight: 6 * dialog.u
                            radius: 3 * dialog.u
                            color: UM.Theme.getColor("lining")
                            Rectangle
                            {
                                width: parent.width * Math.max(0.04, modelData.score)
                                height: parent.height
                                radius: parent.radius
                                color: UM.Theme.getColor("primary")
                            }
                        }
                    }
                }
            }

            UM.Label { text: manager.rotationText; font: UM.Theme.getFont("medium_bold"); Layout.fillWidth: true; wrapMode: Text.WordWrap }

            Repeater
            {
                model: manager.rows
                UM.Label
                {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: modelData.id + ":  " + modelData.text
                    color: UM.Theme.getColor("text_inactive")
                }
            }

            RowLayout
            {
                spacing: 8 * dialog.u
                PillButton
                {
                    text: "Apply rotation"
                    accent: true
                    enabled: !manager.busy && manager.hasRotation
                    onClicked: manager.applyRotation()
                }
                PillButton
                {
                    text: "Test slice top 3"
                    enabled: !manager.busy
                    onClicked: manager.checkOrientations()
                }
            }
        }

        // ---- orientation hints: which direction matters ----
        ColumnLayout
        {
            Layout.fillWidth: true
            visible: manager.action == "orientation"
            spacing: 4 * dialog.u

            RowLayout
            {
                Layout.fillWidth: true
                spacing: dialog.pad
                UM.Label { text: "Force acts along:" }
                Cura.ComboBox
                {
                    Layout.preferredWidth: 190 * dialog.u
                    model: manager.loadChoices
                    textRole: "text"
                    onActivated: manager.setLoad(manager.loadChoices[index].key)
                }
                UM.Label { text: "Side that must look good:" }
                Cura.ComboBox
                {
                    Layout.preferredWidth: 150 * dialog.u
                    model: manager.visibleChoices
                    textRole: "text"
                    onActivated: manager.setVisible(manager.visibleChoices[index].key)
                }
            }
            UM.Label
            {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("text_inactive")
                text: "Optional. Directions refer to the part as it lies on the plate now. Set the force direction for strength goals, the side for looks."
            }
        }

        // ---- status and errors ----
        RowLayout
        {
            Layout.fillWidth: true
            visible: manager.busy
            spacing: dialog.pad
            BusyIndicator { Layout.preferredWidth: 28 * dialog.u; Layout.preferredHeight: 28 * dialog.u; running: manager.busy }
            UM.Label { text: manager.status; color: UM.Theme.getColor("text_inactive") }
            Item { Layout.fillWidth: true }
            PillButton
            {
                text: "Cancel"
                enabled: manager.status != "Cancelling..."
                onClicked: manager.cancelRequest()
            }
        }

        Rectangle
        {
            Layout.fillWidth: true
            visible: manager.error != ""
            implicitHeight: errorText.implicitHeight + 2 * dialog.pad
            radius: 10 * dialog.u
            color: "transparent"
            border.width: 1
            border.color: UM.Theme.getColor("error")
            UM.Label
            {
                id: errorText
                x: dialog.pad
                y: dialog.pad
                width: parent.width - 2 * dialog.pad
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("error")
                text: manager.error
            }
        }

        // ---- input: the conversation line, then the helpers you can pick yourself ----
        RowLayout
        {
            Layout.fillWidth: true
            spacing: dialog.pad

            Cura.TextField
            {
                id: input
                Layout.fillWidth: true
                enabled: !manager.busy
                placeholderText: "Describe your goal, or answer the question..."
                onAccepted: dialog.askNow("")
            }
            PillButton
            {
                text: "Send"
                accent: true
                enabled: !manager.busy && (input.text.trim().length > 0 || manager.chat.length > 0)
                onClicked: dialog.askNow("")
            }
        }
        // Not part of the input: each runs one helper on the conversation (or on what you typed).
        RowLayout
        {
            Layout.fillWidth: true
            spacing: dialog.pad
            UM.Label
            {
                text: "Run one helper directly:"
                color: UM.Theme.getColor("text_inactive")
            }
            Repeater
            {
                model: [
                    {"text": "Suggest settings", "action": "settings"},
                    {"text": "Suggest orientation", "action": "orientation"},
                    {"text": "Edit part or setup", "action": "parts"}
                ]
                PillButton
                {
                    text: modelData.text
                    enabled: !manager.busy
                    onClicked: dialog.askNow(modelData.action)
                }
            }
            Item { Layout.fillWidth: true }
        }
        }  // conversation view
    }
}
