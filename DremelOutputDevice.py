import os
import json
import threading
import time
import urllib.request
import urllib.error
import socket
from threading import Thread

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
        thread = threading.Thread(target=self._uploadGCode, args=(temp_file, file_name))
        thread.daemon = True
        thread.start()

    def _uploadGCode(self, temp_file, file_name):
        """Upload G-code to the Dremel printer."""
        try:
            # Open the G-code file
            with open(temp_file, "rb") as f:
                gcode_data = f.read()

            # Create a message to show upload progress
            message = Message(catalog.i18nc("@info:status", "Uploading to Dremel printer"), 0, progress=0)
            message.show()

            # Upload the file using multipart form
            boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'

            # Create the multipart form data for the file upload
            content_type = 'multipart/form-data; boundary=%s' % boundary

            # Form data preparation
            form_data = []
            form_data.append('--%s' % boundary)
            form_data.append('Content-Disposition: form-data; name="command"')
            form_data.append('')
            form_data.append('upload')
            form_data.append('--%s' % boundary)
            form_data.append('Content-Disposition: form-data; name="file"; filename="%s"' % file_name)
            form_data.append('Content-Type: application/octet-stream')
            form_data.append('')

            # Join everything except the file content
            form_data_str = '\r\n'.join(form_data)
            form_data_bytes = form_data_str.encode('utf-8')

            # Add the file content and the end boundary
            end_boundary = '\r\n--%s--\r\n' % boundary
            end_boundary_bytes = end_boundary.encode('utf-8')

            # Total content to send
            data = form_data_bytes + b'\r\n' + gcode_data + b'\r\n' + end_boundary_bytes

            # Create the request
            command_url = f"{self._url}command"
            req = urllib.request.Request(command_url, data=data)
            req.add_header('Content-Type', content_type)
            req.add_header('Content-Length', len(data))

            # Send the request
            response = urllib.request.urlopen(req, timeout=30)

            # Check the response
            if response.status == 200:
                message.hide()
                success_message = Message(
                    catalog.i18nc("@info:status", "Print job uploaded to Dremel printer successfully."))
                success_message.show()

                # Start the print job
                req = urllib.request.Request(command_url)
                req.add_header('Content-Type', 'application/x-www-form-urlencoded')
                start_data = f"command=printfile&filename={file_name}".encode('utf-8')
                start_response = urllib.request.urlopen(req, data=start_data, timeout=10)

                if start_response.status == 200:
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
                # Set a reasonable timeout
                socket.setdefaulttimeout(5)

                # Try to connect to the printer
                req = urllib.request.Request(f"{self._url}command")
                req.add_header('Content-Type', 'text/plain')

                response = urllib.request.urlopen(req, data=b'getprinterstatus')

                # Check if connected successfully
                if response.status == 200:
                    # Connected successfully
                    if not self._is_connected:
                        self._is_connected = True
                        self.stateChanged.emit()

                    # Parse printer status
                    status_data = json.loads(response.read().decode('utf-8'))

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
            time.sleep(2)

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