import os
import json
import requests
from threading import Thread
from time import sleep

from UM.i18n import i18nCatalog
from UM.Logger import Logger
from UM.Message import Message
from UM.OutputDevice.OutputDevice import OutputDevice
from UM.OutputDevice import OutputDeviceError
from UM.Application import Application
from UM.Signal import Signal, signalemitter

from cura.CuraApplication import CuraApplication

catalog = i18nCatalog("uranium")


@signalemitter
class DremelOutputDevice(OutputDevice):
    """Output device for Dremel 3D printers."""

    # Signals emitted when the printer status changes
    progressChanged = Signal()
    stateChanged = Signal()

    def __init__(self, device_id, name, url, properties=None):
        super().__init__(device_id)

        self._name = name
        self._url = url
        self._properties = properties or {}

        # Set descriptive properties
        self.setShortDescription(catalog.i18nc("@action:button", "Print with Dremel"))
        self.setDescription(catalog.i18nc("@properties:tooltip", "Print with Dremel"))
        self.setIconName("print")

        # Set device priority - make it show up in the front
        self.setPriority(2)

        # Connection status
        self._is_connected = False
        self._status_thread = None
        self._printing = False
        self._progress = 0

        # Start monitoring the printer status
        self._startStatusMonitor()

    def requestWrite(self, nodes, file_name=None, limit_mimetypes=None, file_handler=None, **kwargs):
        """Request to write the given nodes to the printer."""
        if self._printing:
            message = Message(catalog.i18nc("@info:status",
                                            "Dremel printer is busy. Please wait until the current print job is finished."))
            message.show()
            return

        # If no file name is provided, create one
        if file_name is None:
            file_name = self._automaticFileName(nodes)

        # Get the G-code from the scene
        application = CuraApplication.getInstance()
        gcode_writer = application.getOutputDeviceManager().getOutputDevice("local_file")

        if not gcode_writer:
            Logger.log("e", "Failed to find local file output device")
            return

        # Actually export the G-code
        temp_file = application.getTempFile(".gcode")
        gcode_writer.requestWrite(nodes, temp_file, limit_mimetypes, file_handler, **kwargs)

        # Upload the G-code to the printer
        self._uploadGCode(temp_file, file_name)

    def _uploadGCode(self, temp_file, file_name):
        """Upload G-code to the Dremel printer."""
        try:
            # Open the G-code file
            with open(temp_file, "rb") as f:
                gcode_data = f.read()

            # Create a message to show upload progress
            message = Message(catalog.i18nc("@info:status", "Uploading to Dremel printer"), 0, progress=0)
            message.show()

            # Upload the file
            command_url = f"{self._url}command"
            response = requests.post(
                command_url,
                files={"file": (file_name, gcode_data)},
                data={"command": "upload"},
                timeout=10
            )

            if response.status_code == 200:
                message.hide()
                success_message = Message(
                    catalog.i18nc("@info:status", "Print job uploaded to Dremel printer successfully."))
                success_message.show()

                # Start the print job
                start_response = requests.post(
                    command_url,
                    data={"command": "printfile", "filename": file_name},
                    timeout=10
                )

                if start_response.status_code == 200:
                    self._printing = True
                    self._progress = 0
                    self.progressChanged.emit()
                else:
                    error_message = Message(
                        catalog.i18nc("@info:status", "Failed to start print job on Dremel printer."))
                    error_message.show()
            else:
                message.hide()
                error_message = Message(catalog.i18nc("@info:status", "Failed to upload print job to Dremel printer."))
                error_message.show()

        except Exception as e:
            Logger.log("e", f"Error uploading to Dremel printer: {str(e)}")
            error_message = Message(catalog.i18nc("@info:status", f"Error uploading to Dremel printer: {str(e)}"))
            error_message.show()

    def _startStatusMonitor(self):
        """Start monitoring the printer status."""
        if self._status_thread is not None and self._status_thread.is_alive():
            return

        self._status_thread = Thread(target=self._statusThreadFunction, daemon=True)
        self._status_thread.start()

    def _statusThreadFunction(self):
        """Thread function to monitor printer status."""
        while True:
            try:
                # Try to connect to the printer
                response = requests.post(
                    f"{self._url}command",
                    data="getprinterstatus",
                    headers={"Content-Type": "text/plain"},
                    timeout=5
                )

                if response.status_code == 200:
                    # Connected successfully
                    if not self._is_connected:
                        self._is_connected = True
                        self.stateChanged.emit()

                    # Parse printer status
                    status_data = response.json()

                    # Update printing status
                    printing_status = status_data.get("build", {}).get("status", "").lower()
                    self._printing = printing_status in ["building", "printing"]

                    # Update progress if printing
                    if self._printing:
                        progress = status_data.get("build", {}).get("progress", 0)
                        if progress != self._progress:
                            self._progress = progress
                            self.progressChanged.emit()

                else:
                    # Connection failed
                    if self._is_connected:
                        self._is_connected = False
                        self.stateChanged.emit()

            except Exception as e:
                # Connection error
                if self._is_connected:
                    self._is_connected = False
                    self.stateChanged.emit()
                Logger.log("d", f"Error connecting to Dremel printer: {str(e)}")

            # Wait before checking again
            sleep(2)

    def _automaticFileName(self, nodes):
        """Generate an automatic file name based on the model name."""
        application = CuraApplication.getInstance()

        # Get the base name of the first mesh
        for node in nodes:
            if node.getName() and node.getMeshData():
                name = node.getName()
                return f"{name}.gcode"

        # Default if no name is found
        return "dremel_print.gcode"

    def isConnected(self):
        """Return whether the device is connected."""
        return self._is_connected

    @property
    def progress(self):
        """Return the current progress of the print job."""
        return self._progress

    @property
    def is_printing(self):
        """Return whether the printer is currently printing."""
        return self._printing