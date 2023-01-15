function sendLog(message) {
    print(`ktt: > ${message}`);
    callDBus("com.github.syrkuit.ktt", "/KTT", "com.github.syrkuit.ktt", "Log", message, function() {});
}

function screenUpdate() {
    callDBus("com.github.syrkuit.ktt", "/KTT", "com.github.syrkuit.ktt", "ScreenConfiguration",
             workspace.numScreens, workspace.virtualScreenSize.width, workspace.virtualScreenSize.height,
             function() {});
}

function watchClient(client) {
    if (client.specialWindow || client.modal || client.transientFor) return;
    client.activeChanged.connect(function() { changedClient(client); });
    client.captionChanged.connect(function() { if (client.active) changedClient(client); });
}

function changedClient(client) {
    assertFalse(client.specialWindow, `changedclient called for specialWindow: ${client.caption}`);
    assertNull(client.transientFor, `changedclient called for transientFor: ${client.caption}`);
    assertFalse(client.modal, `changedclient called for modal: ${client.caption}`);
    let clients = workspace.clientList();
    for (let i = 0; i < clients.length; i++) {
        // TODO: regexp should be configurable
        if (clients[i].caption && clients[i].caption.search(/^Meet - .+ - Google Chrome$/) == 0) {
            if (client.active)
                callDBus("com.github.syrkuit.ktt", "/KTT", "com.github.syrkuit.ktt",
                         "Focus", workspace.currentDesktop, clients[i].caption);
            return;
        }
    }
    if (client.active) {
        callDBus("com.github.syrkuit.ktt", "/KTT", "com.github.syrkuit.ktt",
                 "Focus", workspace.currentDesktop, client.caption);
    } else {
        callDBus("com.github.syrkuit.ktt", "/KTT", "com.github.syrkuit.ktt", "FocusLost");
    }
}

workspace.numberScreensChanged.connect(x => screenUpdate());
workspace.virtualScreenSizeChanged.connect(screenUpdate);
workspace.clientAdded.connect(watchClient);
workspace.clientList().forEach(client => watchClient(client))

sendLog("KWin script started");
