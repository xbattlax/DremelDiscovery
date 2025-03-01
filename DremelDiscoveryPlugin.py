import os
import json
import threading
import time
import urllib.request
import urllib.error
import socket
from typing import Dict, List, Optional

from UM.i18n import i18nCatalog
from UM.Logger import Logger
from UM.Message import Message
from UM.OutputDevice.OutputDevicePlugin import OutputDevicePlugin
from UM.Signal import Signal, signalemitter

from cura.CuraApplication import CuraApplication

from . import DremelOutputDevice

catalog = i18nCatalog("uranium")


@signalemitter
class DremelDiscoveryPlugin(OutputDevicePlugin):
    """Plugin for discovering Dremel 3D printers on the local network using standard libraries."""

    discoveredDevicesChanged = Signal()

    def __init__(self):
        super().__init__()
        self._discovered_devices = {}
        self._discovery_thread = None
        self._is_scanning = False

        # Create settings
        application = CuraApplication.getInstance()
        self._global_container_stack = None
        application.globalContainerStackChanged.connect(self._onGlobalContainerStackChanged)

        # Listen to when the user clicks the "Connect" button
        self.getOutputDeviceManager().outputDevicesChanged.connect(self._outputDevicesChanged)

    def start(self):
        """Start the discovery process."""
        self.startDiscovery()

    def stop(self):
        """Stop the discovery process."""
        self.stopDiscovery()

    def startDiscovery(self):
        """Start the Dremel printer discovery process."""
        if self._is_scanning:
            return

        self._is_scanning = True

        # Start the discovery in a separate thread, to not block the interface
        if self._discovery_thread is None:
            Logger.log("i", "Starting Dremel printer discovery")
            self._discovery_thread = threading.Thread(target=self._discoverDremelPrinters)
            self._discovery_thread.daemon = True
            self._discovery_thread.start()

    def stopDiscovery(self):
        """Stop the discovery process."""
        if not self._is_scanning:
            return

        self._is_scanning = False
        self._discovery_thread = None

    def _checkPrinter(self, ip):
        """Check if the given IP has a Dremel printer."""
        url = f"http://{ip}:80/command"
        printer_found = False

        try:
            # Set a short timeout to avoid hanging
            socket.setdefaulttimeout(1)

            # Prepare the request with text/plain content type
            req = urllib.request.Request(url)
            req.add_header('Content-Type', 'text/plain')

            # Send the getprinterstatus command
            response = urllib.request.urlopen(req, data=b'getprinterstatus')

            # Read and parse the JSON response
            json_response = json.loads(response.read().decode('utf-8'))

            # If we get a valid response, it's likely a Dremel printer
            if json_response:
                printer_url = f"http://{ip}/"

                # Get printer name and other info if available
                printer_name = json_response.get("machine", {}).get("name", f"Dremel {ip}")

                self._onDeviceFound(ip, printer_name, printer_url, json_response)
                Logger.log("i", f"Found Dremel printer at {printer_url}")
                printer_found = True

        except urllib.error.URLError:
            # This is normal for most IPs that don't have a Dremel printer
            pass
        except socket.timeout:
            # This is also normal for most IPs
            pass
        except Exception as e:
            # Something else went wrong
            Logger.log("d", f"Error while checking {url}: {str(e)}")

        return printer_found

    def _discoverDremelPrinters(self):
        """Discover Dremel printers on the network."""
        Logger.log("i", "Starting Dremel printer discovery process")

        devices_found = 0

        # Using the same IP range as your original code (2-250)
        for i in range(2, 250):
            if not self._is_scanning:
                break

            ip = f"192.168.1.{i}"
            if self._checkPrinter(ip):
                devices_found += 1

        Logger.log("i", f"Dremel printer discovery complete. Found {devices_found} devices")
        self._is_scanning = False

    def _onDeviceFound(self, ip, name, url, properties=None):
        """Called when a Dremel printer is found."""
        if properties is None:
            properties = {}

        # Create a unique key for this device
        key = f"dremel:{ip}"

        # Check if this device is already added
        if key not in self._discovered_devices:
            # Add the device to the list
            device = DremelOutputDevice.DremelOutputDevice(key, name, url, properties)
            self._discovered_devices[key] = device

            # Add the device to Cura
            self.getOutputDeviceManager().addOutputDevice(device)

            self.discoveredDevicesChanged.emit()

    def _removeDevice(self, device_id):
        """Remove a device from the list."""
        if device_id in self._discovered_devices:
            device = self._discovered_devices.pop(device_id)
            self.getOutputDeviceManager().removeOutputDevice(device_id)
            self.discoveredDevicesChanged.emit()

    def _onGlobalContainerStackChanged(self):
        """Called when the selected printer changes."""
        self._global_container_stack = CuraApplication.getInstance().getGlobalContainerStack()

    def _outputDevicesChanged(self):
        """Called when the list of output devices changes."""
        # This can be used to perform actions when the user connects to a printer
        pass