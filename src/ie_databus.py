import json
from dataclasses import dataclass
from threading import Event, Lock
from typing import Dict, Any

import paho.mqtt.client as mqtt


@dataclass
class Tag:
    """Object for organizing and providing easy access to the data received
    from MQTT

    Attributes
    ----------
    name : str
        The name of the PLC tag
    id: str
        The ID of the PLC tag
    data_type: str
        The original data type of the val attribute
    qc: int
    ts: str
        The timestamp of when this data was received
    val: float
        The current value of the PLC tag
    """

    name: str
    id: str
    data_type: str
    qc: int
    ts: str
    val: float


class IEDatabus:
    """The main object for interfacing with an edge device's IE Databus in Python

    Attributes
    ----------
    write_topic : str
        Used to change which MQTT topic `write_to_tag` publishes to
    _client : mqtt.Client
        The underlying MQTT client that connects to the IE Databus
    _tags : Dict[str, Tag]
        The underlying tag dictionary that is thread protected by a
        `property` and a `Lock`
    _tag_headers : Dict[str, Dict[str, str]]
        A dictionary that contains the header data that is received once
        when the MQTT client first connects to the IE Databus; this data
        allows for the correct mapping of a tag's name to its ID
    _tags_lock : threading.Lock
        The Lock object that makes the `tags` attribute thread-safe
    _ready_event : threading.Event
        Becomes set once the client receives enough data from the IE Databus
        to populate the `tags` attribute
    """

    def __init__(self, username: str, password: str):
        """
        Parameters
        ----------
        username : str
            The username required for connecting to the IE Databus' MQTT broker
        password : str
            The password required for connecting to the IE Databus' MQTT broker
        """

        # mqtt client setup
        self._client = mqtt.Client()
        self._client.username_pw_set(username, password)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.connect('ie-databus')

        # setup tag access vars
        self._tags: Dict[str, Tag] = {}
        self._tag_headers: Dict[str, Dict[str, str]] = {}
        self._tags_lock = Lock()
        self._ready_event = Event()

        # public class vars
        self.write_topic = 'ie/d/j/simatic/v1/s7c1/dp/w/USC_PLC'

    @property
    def tags(self) -> Dict[str, Tag]:
        """A property that provides access to all the PLC tags and their
        values in real time

        Returns
        -------
        Dict[str, Tag]
            A mapping between the name of the tag and the `Tag` object associated
            with it
        """

        with self._tags_lock:
            value = self._tags.copy()
        return value

    @tags.setter
    def tags(self, value: Dict[str, Tag]):
        """A thread-safe solution to exposing the real-time tag data to this
        API

        Parameters
        ----------
        value : Dict[str, Tag]
            The new tag dictionary to update the old one with
        """

        with self._tags_lock:
            self._tags = value

    def start(self):
        """Start listening for incoming MQTT data on the IE Databus"""

        self._client.loop_start()
        self._ready_event.wait()

    def stop(self):
        """Stop listening for incoming MQTT data on the IE Databus"""

        self._client.loop_stop()

    def write_to_tag(self, tag: str, data: Any):
        """Writes serializable data to a specific PLC tag

        This method blocks until the data has been published

        Parameters
        ---------
        tag : str
            The name of the PLC tag to write the data to
        data : Any
            The data to send to the specified PLC tag
        """

        payload = {'seq': 1, 'vals': [{'id': self.tags[tag].id, 'val': data}]}
        msg_info = self._client.publish(self.write_topic, json.dumps(payload))
        msg_info.wait_for_publish()

    def _on_connect(self, client, userdata, flags, rc):
        """An override method for connecting to the MQTT broker"""

        if rc == 0:
            print('Connected successfully')
        else:
            print('Error: ' + str(rc))
        client.subscribe('ie/#')

    def _on_message(self, client, userdata, msg):
        """An override method for receiving a message from the MQTT broker"""

        if msg.topic == self.write_topic:
            return
        data = json.loads(msg.payload.decode())
        if len(self._tag_headers) == 0:
            try:
                dpds = data['connections'][0]['dataPoints'][0][
                    'dataPointDefinitions']
            except KeyError:
                pass
            else:
                for data_point in dpds:
                    self._tag_headers[data_point['id']] = data_point
        else:
            # create tags
            tags = {}
            for value_dict in data['vals']:
                header = self._tag_headers[value_dict['id']]
                tags[header['name']] = Tag(name=header['name'],
                                           id=header['id'],
                                           data_type=header['dataType'],
                                           qc=value_dict['qc'],
                                           ts=value_dict['ts'],
                                           val=value_dict['val'])
            self.tags = tags
            self._ready_event.set()


if __name__ == '__main__':
    databus = IEDatabus('edge', 'edge')
    databus.start()

    for key, tag in databus.tags.items():
        print(f'{key}: {tag.val}')
