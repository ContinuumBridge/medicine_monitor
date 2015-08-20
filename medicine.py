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
        [
            {
                "name": "Morning",
                "start": "07:00", 
                "end": "08:00"
            },
            {
                "name": "Evening",
                "start": "21:00", 
                "end": "22:00"
            }
        ]
    ],
    "reminders": True,
    "alerts": True,
    "accel_min_change": 0.2,  # The min change in g on any axis to indicate that medicine is being taken
    "ignore_time": 120        # Movement within this interval will not set another alert
}

import sys
import os.path
import time
from cbcommslib import CbApp, CbClient
from cbutils import betweenTimes
from cbconfig import *
import json
from twisted.internet import reactor

CONFIG_FILE                       = CB_CONFIG_DIR + "medicine_monitor.config"
CID                               = "CID164"  # Client ID

class Medicine():
    def __init__(self, bridge_id):
        self.bridge_id = bridge_id
        self.s = []
        self.waiting = False
        self.lastValues = [0.0, 0.0, 0.0]
        self.lastTime = time.time()
        self.taken = False

    def onChange(self, timeStamp, values):
        try:
            moved = False
            for v in values:
                if abs(values[v] - self.lastValues[v]) > config["accel_min_change"]:
                    moved = True
            if moved:
                if timeStamp - self.lastTime > config["ignore_time"]:
                    self.lastTime = timeStamp:
                    self.taken = True
            self.lastValues = value:
        except Exception as ex:
            self.cbLog("warning", "medicine onChange encountered problems. Exception: " + str(type(ex)) + str(ex.args))

    def monitor(self):
        # Called every 10 seconds
        try:
            if self.taken:
                self.taken = False
                slotName = None
                for s in config["time_slots"]:
                    if betweenTimes(self.lastTime, s["start"], s["end"]):
                        slotName = s["name"]
                        break
                name = config["medicine_name"].replace(" ", "_")
                if slotName:
                    values = {
                        "name": self.bridge_id + "/" + name + "/" + in_slot,
                        "points": [[int(timeStamp*1000), 1]]
                    }
                else:
                    values = {
                        "name": self.bridge_id + "/" + name + "/" + out_slot,
                        "points": [[int(timeStamp*1000), 1]]
                self.storeValues(values)
        except Exception as ex:
            self.cbLog("warning", "medicine monitor encountered problems in taken. Exception: " + str(type(ex)) + str(ex.args))

    def sendValues(self):
        msg = {"m": "data",
               "d": self.s
               }
        #self.cbLog("debug", "sendValues. Sending: " + str(json.dumps(msg, indent=4)))
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
        self.devServices = [] 
        self.idToName = {} 
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
        if message["characteristic"] == "acceleration":
            self.medicine.onChange(message["timeStamp"], message["data"])

    def onAdaptorService(self, message):
        #self.cbLog("debug", "onAdaptorService, message: " + str(json.dumps(message, indent=4)))
        if self.state == "starting":
            self.setState("running")
        self.devServices.append(message)
        serviceReq = []
        power = False
        biinary = False
        for p in message["service"]:
            if p["characteristic"] == "acceleration":
                self.medicine.gotSensor = True
            serviceReq.append({"characteristic": "acceleration", "interval": 2})
        msg = {"id": self.id,
               "request": "service",
               "service": serviceReq}
        self.sendMessage(msg, message["id"])
        self.cbLog("debug", "onAdaptorService, response: " + str(json.dumps(msg, indent=4)))

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
            if str(config[c]).lower() in ("true", "t", "1"):
                config[c] = True
            elif str(config[c]).lower() in ("false", "f", "0"):
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
        self.medicine = medicine(self.bridge_id)
        self.medicine.cbLog = self.cbLog
        self.medicine.client = self.client
        self.medicine.setNames(idToName2)
        self.setState("starting")

if __name__ == '__main__':
    App(sys.argv)
