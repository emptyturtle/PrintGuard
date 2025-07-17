import asyncio
import logging

import requests

from ..models import PrinterType, PollingTask, SavedConfig, AlertAction
from .camera_utils import get_camera_state_sync, update_camera_state
from .config import PRINTER_STAT_POLLING_RATE_MS, get_config
from .printer_services.octoprint import OctoPrintClient
from .sse_utils import add_polling_task, sse_update_printer_state
from .printer_services.prusalink import PrusaLinkPyClient


# This dictionary maps each printer type to its client class.
CLIENT_FACTORY = {
    PrinterType.OCTOPRINT: OctoPrintClient,
    PrinterType.PRUSALINKPY: PrusaLinkPyClient,
    # more future printer types can be added here
}

def get_printer_config(camera_uuid):
    """Retrieve printer configuration from camera state.

    Args:
        camera_uuid (str): The UUID of the camera.

    Returns:
        dict or None: The printer_config dictionary if set, otherwise None.
            Structure of printer_config example:
            {
                'printer_type': str,
                'base_url': str,
                'api_key': str,
                'name': str
            }
    """
    camera_state = get_camera_state_sync(camera_uuid)
    if camera_state and hasattr(camera_state, 'printer_config') and camera_state.printer_config:
        return camera_state.printer_config
    return None

def get_printer_id(camera_uuid):
    """Retrieve the printer ID associated with a camera.

    Args:
        camera_uuid (str): The UUID of the camera.

    Returns:
        str or None: The printer_id if set, otherwise None.
    """
    camera_state = get_camera_state_sync(camera_uuid)
    if camera_state and hasattr(camera_state, 'printer_id') and camera_state.printer_id:
        return camera_state.printer_id
    return None

async def set_printer(camera_uuid, printer_id, printer_config):
    """Associate a printer with a camera and persist in state.

    Args:
        camera_uuid (str): The UUID of the camera.
        printer_id (str): The unique identifier for the printer.
        printer_config (dict): The configuration details for the printer.

    Returns:
        Optional[CameraState]: The updated camera state, or None if failed.
    """
    return await update_camera_state(camera_uuid, {
        "printer_id": printer_id,
        "printer_config": printer_config
    })

async def remove_printer(camera_uuid):
    """Remove the printer association from a camera.

    Args:
        camera_uuid (str): The UUID of the camera.

    Returns:
        Optional[CameraState]: The updated camera state, or None if failed.
    """
    return await update_camera_state(camera_uuid, {
        "printer_id": None,
        "printer_config": None
    })

async def poll_printer_state_func(client, interval, stop_event):
    """Continuously poll the printer state and send updates via SSE.

    Args:
        client (object): The printer client object (e.g., OctoPrintClient, PrusaLinkPyClient).
        interval (float): Time in seconds between polls.
        stop_event (asyncio.Event): An event to signal polling should stop.
    """
    while not stop_event.is_set():
        try:
            current_printer_state = client.get_printer_state()
            await sse_update_printer_state(current_printer_state)
        except Exception as e:
            logging.warning("Error polling printer state: %s", str(e))
        await asyncio.sleep(interval)

async def start_printer_state_polling(camera_uuid):
    """Start background polling of printer state for a camera."""
    stop_event = asyncio.Event()
    camera_printer_config = get_printer_config(camera_uuid)
    if not camera_printer_config:
        logging.warning("No printer configuration found for camera UUID %s", camera_uuid)
        return

    config = get_config()
    printer_polling_rate = float(config.get(
        SavedConfig.PRINTER_STAT_POLLING_RATE_MS, PRINTER_STAT_POLLING_RATE_MS
        ) / 1000)

    printer_type_str = camera_printer_config.get('printer_type')
    printer_type = PrinterType(printer_type_str) if printer_type_str else PrinterType.OCTOPRINT

    # Look up the correct client class in the factory.
    client_class = CLIENT_FACTORY.get(printer_type)

    if not client_class:
        logging.error(f"Unsupported printer type: {printer_type}")
        return

    # Create an instance of the found class.
    client = client_class(
        camera_printer_config.get('base_url'),
        camera_printer_config.get('api_key')
    )

    task = asyncio.create_task(poll_printer_state_func(client, printer_polling_rate, stop_event))
    add_polling_task(camera_uuid, PollingTask(task=task, stop_event=stop_event))
    logging.debug(f"Started printer state polling for camera index %d using {client_class.__name__}", camera_uuid)


def suspend_print_job(camera_uuid, action: AlertAction):
    """Pause or cancel an ongoing print job based on an alert action.

    Args:
        camera_uuid (str): The UUID of the camera associated with the printer.
        action (AlertAction): The action to perform (CANCEL_PRINT or PAUSE_PRINT).

    Returns:
        bool: True if the job was suspended successfully or no job was active, False otherwise.
    """
    printer_config = get_printer_config(camera_uuid)
    if not printer_config:
        logging.error("No printer configuration found for camera UUID %s", camera_uuid)
        return False

    printer_type_str = printer_config.get('printer_type')
    # default to octoPrint if the type is not specified for backward compatibility
    printer_type = PrinterType(printer_type_str) if printer_type_str else PrinterType.OCTOPRINT

    client_class = CLIENT_FACTORY.get(printer_type)

    if not client_class:
        logging.error(f"Unsupported printer type: {printer_type}")
        return False

    client = client_class(
        printer_config.get('base_url'),
        printer_config.get('api_key')
    )

    try:
        job_info = client.get_job_info()
        if job_info.state not in ["Printing", "PRINTING"]:
            logging.debug("No active print job to suspend for camera %s.", camera_uuid)
            return True

        match action:
            case AlertAction.CANCEL_PRINT:
                client.cancel_job()
                logging.debug("Print cancelled for printer on camera %s.", camera_uuid)
                return True
            case AlertAction.PAUSE_PRINT:
                client.pause_job()
                logging.debug("Print paused for printer on camera %s.", camera_uuid)
                return True
            case _:
                return True
    except Exception as e:
        logging.error("Error suspending print job for printer on camera %s: %s", camera_uuid, e)
        return False

    return False
