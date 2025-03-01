# Copyright (c) 2023
# The DremelDiscovery plugin is released under the terms of the LGPLv3 or higher.

from . import DremelDiscoveryPlugin

def getMetaData():
    return {
        "plugin": {
            "name": "Dremel Printer Connection",
            "author": "Your Name",
            "version": "1.0.0",
            "description": "Allows you to connect to Dremel 3D printers on your local network",
            "api": 5,  # API version for Cura
            "supported_sdk_versions": ["7.0.0", "8.0.0"]  # Compatible Cura SDK versions
        }
    }

def register(app):
    return {"output_device": DremelDiscoveryPlugin.DremelDiscoveryPlugin()}