#!/usr/bin/env python
# medicine.py
# Copyright (C) ContinuumBridge Limited, 2015 - All Rights Reserved
# Written by Peter Claydon
#

# Default values:
config = {
    "medicine": True,
    "medicine_name": "My_Medicine",
    "time_slots": [
        {
            "name": "Morning",
            "start": "07:00", 
            "end": "08:00"
        },
        {
            "name": "Evening",
            "start": "18:00", 
            "end": "22:00"
        }
    ],
    "reminders": True,
    "reminder_time": 600,
    "accel_min_change": 0.2,  # The min change in g on any axis to indicate that medicine is being taken
    "ignore_time": 30,        # Movement within this interval will not set another alert
    "data_send_delay": 1
}

import sys
import os.path
import time
from cbcommslib import CbApp, CbClient
from cbutils import betweenTimes, hourMin2Epoch, nicetime
from cbconfig import *
import json
from twisted.internet import reactor

CONFIG_FILE                       = CB_CONFIG_DIR + "medicine_monitor.config"
CID                               = "CID164"  # Client ID

class Medicine():
    def __init__(self, bridge_id, name):
        self.bridge_id = bridge_id
        self.name = name
        self.s = []
        self.waiting = False
        self.starting = 0
        self.lastValues = [0.0, 0.0, 0.0]
        self.lastTime = 0
        self.taken = False
        self.lastReminderTime = 0
        reactor.callLater(1, self.monitor)

    def onChange(self, timeStamp, values):
        #self.cbLog("debug", "onChange. values: " + str(values))
        try:
            if self.starting < 3:
                self.lastValues = values
                self.starting += 1
                return
            moved = False
            for v in range(0,3):
                if abs(values[v] - self.lastValues[v]) > config["accel_min_change"]:
                    moved = True
            if moved:
                if timeStamp - self.lastTime > config["ignore_time"]:
                    self.lastTime = timeStamp
                    self.taken = True
            self.lastValues = values
        except Exception as ex:
            self.cbLog("warning", "medicine onChange encountered problems. Exception: " + str(type(ex)) + str(ex.args))

    def monitor(self):
        # Called every 5 seconds
        #self.cbLog("debug", "monitor. taken: " + str(self.taken))
        try:
            if self.taken:
                self.taken = False
                inSlot = False
                for s in config["time_slots"]:
                    if betweenTimes(self.lastTime, s["start"], s["end"]):
                        inSlot = True
                        break
                joinedName = self.name.replace(" ", "_")
                if inSlot:
                    values = {
                        "name": self.bridge_id + "/" + joinedName + "/" + "in_slot",
                        "points": [[int(self.lastTime*1000), 1]]
                    }
                else:
                    values = {
                        "name": self.bridge_id + "/" + joinedName + "/" + "out_slot",
                        "points": [[int(self.lastTime*1000), 1]]
                    }
                self.storeValues(values)
        except Exception as ex:
            self.cbLog("warning", "medicine monitor encountered problems in taken. Exception: " + str(type(ex)) + str(ex.args))
        try:
            if config["reminders"]:
                now = time.time()
                if now - self.lastReminderTime > config["reminder_time"] + 10:
                    for s in config["time_slots"]:
                        if betweenTimes(now, s["start"], s["end"]):
                            if not betweenTimes(self.lastTime, s["start"], s["end"]):
                                epochEnd = hourMin2Epoch(s["end"])
                                if epochEnd - now < config["reminder_time"]:
                                    self.cbLog("debug", "monitor. start: " + str(s["start"]) + ", end: " + str(s["end"]))
                                    msg = {"m": "alert",
                                           "a": "Remember to take your " + self.name + " by " + nicetime(epochEnd)[:5],
                                           "t": now
                                          }
                                    self.client.send(msg)
                                    self.cbLog("debug", "msg send to client: " + str(json.dumps(msg, indent=4)))
                                    self.lastReminderTime = now
                                    break
        except Exception as ex:
            self.cbLog("warning", "medicine monitor encountered problems in reminders. Exception: " + str(type(ex)) + str(ex.args))
        reactor.callLater(5, self.monitor)

    def sendValues(self):
        msg = {"m": "data",
               "d": self.s
               }
        self.cbLog("debug", "sendValues. Sending: " + str(json.dumps(msg, indent=4)))
        self.client.send(msg)
        self.s = []
        self.waiting = False

    def storeValues(self, values):
        self.s.append(values)
        if not self.waiting:
            self.waiting = True
            reactor.callLater(config["data_send_delay"], self.sendValues)

class App(CbApp):
    def __init__(self, argv):
        self.appClass = "monitor"
        self.state = "stopped"
        self.status = "ok"
        self.devices = []
        self.idToName = {} 
        self.medicine = {} 
        #CbApp.__init__ MUST be called
        CbApp.__init__(self, argv)

    def setState(self, action):
        if action == "clear_error":
            self.state = "running"
        else:
            self.state = action
        msg = {"id": self.id,
               "status": "state",
               "state": self.state}
        self.sendManagerMessage(msg)

    def onConcMessage(self, message):
        #self.cbLog("debug", "onConcMessage, message: " + str(json.dumps(message, indent=4)))
        if "status" in message:
            if message["status"] == "ready":
                # Do this after we have established communications with the concentrator
                msg = {
                    "m": "req_config",
                    "d": self.id
                }
                self.client.send(msg)
        self.client.receive(message)

    def onClientMessage(self, message):
        #self.cbLog("debug", "onClientMessage, message: " + str(json.dumps(message, indent=4)))
        global config
        if "config" in message:
            if "warning" in message["config"]:
                self.cbLog("warning", "onClientMessage: " + str(json.dumps(message["config"], indent=4)))
            else:
                try:
                    config = message["config"]
                    with open(CONFIG_FILE, 'w') as f:
                        json.dump(config, f)
                    self.cbLog("info", "Config updated")
                except Exception as ex:
                    self.cbLog("warning", "onClientMessage, could not write to file. Type: " + str(type(ex)) + ", exception: " +  str(ex.args))
                self.readLocalConfig()

    def onAdaptorData(self, message):
        #self.cbLog("debug", "onAdaptorData, message: " + str(json.dumps(message, indent=4)))
        try:
            if message["characteristic"] == "acceleration":
                self.medicine[message["id"]].onChange(message["timeStamp"], [message["data"]["x"], message["data"]["y"], message["data"]["z"]])
        except Exception as ex:
            self.cbLog("warning", "onAdaptorData problem. Exception: " + str(type(ex)) + str(ex.args))

    def onAdaptorService(self, message):
        #self.cbLog("debug", "onAdaptorService, message: " + str(json.dumps(message, indent=4)))
        try:
            if self.state == "starting":
                self.setState("running")
            serviceReq = []
            for p in message["service"]:
                if p["characteristic"] == "acceleration":
                    self.medicine[message["id"]].gotSensor = True
                    serviceReq.append({"characteristic": "acceleration", "interval": 2})
            msg = {"id": self.id,
                   "request": "service",
                   "service": serviceReq}
            self.sendMessage(msg, message["id"])
            self.cbLog("debug", "onAdaptorService, response: " + str(json.dumps(msg, indent=4)))
        except Exception as ex:
            self.cbLog("warning", "onAdaptorServices problem. Exception: " + str(type(ex)) + str(ex.args))

    def readLocalConfig(self):
        global config
        try:
            with open(CONFIG_FILE, 'r') as f:
                newConfig = json.load(f)
                self.cbLog("debug", "Read local config")
                config.update(newConfig)
        except Exception as ex:
            self.cbLog("warning", "Local config does not exist or file is corrupt. Exception: " + str(type(ex)) + str(ex.args))
        for c in config:
            if str(config[c]).lower() in ("true", "t"):
                config[c] = True
            elif str(config[c]).lower() in ("false", "f"):
                config[c] = False
        self.cbLog("debug", "Config: " + str(json.dumps(config, indent=4)))

    def onConfigureMessage(self, managerConfig):
        self.readLocalConfig()
        idToName2 = {}
        for adaptor in managerConfig["adaptors"]:
            adtID = adaptor["id"]
            if adtID not in self.devices:
                # Because managerConfigure may be re-called if devices are added
                name = adaptor["name"]
                friendly_name = adaptor["friendly_name"]
                self.cbLog("debug", "managerConfigure app. Adaptor id: " +  adtID + " name: " + name + " friendly_name: " + friendly_name)
                idToName2[adtID] = friendly_name
                self.idToName[adtID] = friendly_name.replace(" ", "_")
                self.devices.append(adtID)
        self.client = CbClient(self.id, CID, 5)
        self.client.onClientMessage = self.onClientMessage
        self.client.sendMessage = self.sendMessage
        self.client.cbLog = self.cbLog
        for a in idToName2:
            self.medicine[a] = Medicine(self.bridge_id, idToName2[a])
            self.medicine[a].cbLog = self.cbLog
            self.medicine[a].client = self.client
        self.setState("starting")

if __name__ == '__main__':
    App(sys.argv)
