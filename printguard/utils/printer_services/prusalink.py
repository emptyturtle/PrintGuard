from typing import Dict
import PrusaLinkPy
from ...models import (FileInfo, JobInfoResponse,
                       TemperatureReading,
                       PrinterState, PrinterTemperatures)


class PrusaLinkPyClient:
    """
    A client for interacting with the PrusaLink API using the PrusaLinkPy library.

    This class provides methods to control and monitor 3D printers through
    the PrusaLink interface, including job management, temperature monitoring,
    and printer state retrieval.

    Attributes:
        client (PrusaLinkPy.PrusaLinkPy): The instance of the PrusaLinkPy client.
    """

    def __init__(self, base_url: str, api_key: str):
        """
        Initializes the PrusaLink client.

        Args:
            base_url (str): The hostname or IP address of the PrusaLink instance.
            api_key (str): The API key for authentication with PrusaLink.
        """
        # The PrusaLinkPy library handles URL formatting internally.
        self.client = PrusaLinkPy.PrusaLinkPy(base_url, api_key)


    def get_job_info(self) -> JobInfoResponse:
        """
        Retrieves information about the current print job.

        Returns:
            JobInfoResponse: Complete job information including progress, file details,
                             and print statistics.

        Raises:
            requests.HTTPError: If the API request fails.
            KeyError: If the response JSON does not contain expected keys (e.g., no active job).
        """
        # PrusaLink splits job information across /status and /job endpoints.
        status_resp = self.client.get_status()
        status_resp.raise_for_status()
        status_data = status_resp.json()

        job_resp = self.client.get_job()
        job_resp.raise_for_status()
        job_data = job_resp.json()

        # Combine data to replicate the JobInfoResponse structure.
        # This will raise an error if 'job' is 'None' in the status response (which is intended).
        progress_info = status_data['job']
        job_details = job_data.get('job', {})

        # Create a dictionary that matches the JobInfoResponse structure.
        response_dict = {
            "job": job_details,
            "progress": {
                "completion": progress_info.get('progress'),
                "printTime": progress_info.get('time_printing'),
                "printTimeLeft": progress_info.get('time_remaining')
            },
            "state": status_data.get('printer', {}).get('state')
        }
        return JobInfoResponse(**response_dict)

    def cancel_job(self) -> None:
        """
        Cancels the currently running print job.

        This will immediately stop the current print job.

        Raises:
            requests.HTTPError: If the API request fails.
        """
        resp = self.client.stop_print()
        if resp.status_code == 204:
            return
        resp.raise_for_status()

    def pause_job(self) -> None:
        """
        Pauses the currently running print job.

        This will temporarily halt the current print job, allowing it to be
        resumed later.

        Raises:
            requests.HTTPError: If the API request fails.
        """
        resp = self.client.pause_print()
        if resp.status_code == 204:
            return
        resp.raise_for_status()

    def get_printer_temperatures(self) -> Dict[str, TemperatureReading]:
        """
        Retrieves current temperature readings from all printer components.

        Returns:
            Dict[str, TemperatureReading]: A dictionary mapping component names
                                           (e.g., 'tool0', 'bed') to their temperature readings.
                                           Returns an empty dict if the printer is not operational.

        Raises:
            requests.HTTPError: If the API request fails (except for 409 conflicts).
        """
        resp = self.client.get_printer()
        if resp.status_code == 409:
            return {}
        resp.raise_for_status()
        data = resp.json()

        # PrusaLink nests temperatures under a 'temperature' key.
        temp_data = data.get("temperature", {})
        if not temp_data:
            return {}

        # Convert the data into TemperatureReading objects.
        readings = {}
        for key, value in temp_data.items():
            if isinstance(value, dict) and 'actual' in value:
                readings[key] = TemperatureReading(**value)
        return readings


    def percent_complete(self) -> float:
        """
        Gets the completion percentage of the current print job.

        Returns:
            float: Completion percentage (0.0 to 100.0).

        Raises:
            requests.HTTPError: If the API request fails.
        """
        # PrusaLink provides progress as a float between 0.0 and 1.0.
        completion = self.get_job_info().progress.completion
        return completion * 100 if completion is not None else 0.0

    def current_file(self) -> FileInfo:
        """
        Gets information about the currently loaded file.

        Returns:
            FileInfo: Details about the file being printed.

        Raises:
            requests.HTTPError: If the API request fails.
        """
        # The `job` key contains the file information.
        file_data = self.get_job_info().job.get("file", {})
        return FileInfo(**file_data)

    def nozzle_and_bed_temps(self) -> Dict[str, float]:
        """
        Gets simplified temperature readings for the nozzle and bed.

        Returns:
            Dict[str, float]: Dictionary with keys:
                - 'nozzle_actual': Current nozzle temperature
                - 'nozzle_target': Target nozzle temperature
                - 'bed_actual': Current bed temperature
                - 'bed_target': Target bed temperature
                Returns 0.0 for all values if temperatures are unavailable.
        """
        temps = self.get_printer_temperatures()
        if not temps:
            return {
                "nozzle_actual": 0.0, "nozzle_target": 0.0,
                "bed_actual": 0.0, "bed_target": 0.0,
            }
        tool0 = temps.get("tool0")
        bed = temps.get("bed")
        return {
            "nozzle_actual": tool0.actual if tool0 else 0.0,
            "nozzle_target": tool0.target if tool0 else 0.0,
            "bed_actual": bed.actual if bed else 0.0,
            "bed_target": bed.target if bed else 0.0,
        }

    def get_printer_state(self) -> PrinterState:
        """
        Gets comprehensive printer state information.

        This method combines job information and temperature readings into
        a unified printer state object.

        Returns:
            PrinterState: Complete printer state. `jobInfoResponse` may be None if
                          retrieval fails (e.g., no active job).
        """
        temperature_readings = self.get_printer_temperatures()
        tool0_temp = temperature_readings.get("tool0")
        bed_temp = temperature_readings.get("bed")

        printer_temps = PrinterTemperatures(
            nozzle_actual=tool0_temp.actual if tool0_temp else None,
            nozzle_target=tool0_temp.target if tool0_temp else None,
            bed_actual=bed_temp.actual if bed_temp else None,
            bed_target=bed_temp.target if bed_temp else None
        )

        try:
            job_info = self.get_job_info()
        except Exception:
            job_info = None

        printer_state = PrinterState(
            jobInfoResponse=job_info,
            temperatureReading=printer_temps
        )
        return printer_state