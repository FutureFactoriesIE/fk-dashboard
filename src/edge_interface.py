import logging
from dataclasses import dataclass
from queue import Queue, Empty
from threading import Thread, Event
from typing import Any, Callable, Optional, Dict, Union

from flask import Flask, render_template, request


class PageAlreadyExists(Exception):
    """Raised when a requested new subdirectory already exists"""
    pass


class MissingMainPage(Exception):
    """Raised when the main page ("/") of the application does not exist"""
    pass


@dataclass
class Command:
    """A container to streamline sending organized data to the web app's javascript

    Attributes
    ----------
    topic : str
        A category of data being sent, its purpose is similar to a MQTT topic
    payload : Any
        The raw data to be sent for processing
    """

    topic: str
    payload: Any

    @classmethod
    def javascript(cls, js: str):
        """A class method to quickly create a Javascript command

        Parameters
        ----------
        js : str
            The javascript to execute
        """

        return cls(topic='javascript', payload=js)

    @classmethod
    def update_interval(cls, interval: int):
        """A class method to quickly create an update interval command

        Parameters
        ----------
        interval : int
            How often the page should check for a new command (in millis)
        """

        return cls(topic='update_interval', payload=interval)


class EventLoopMessage:
    """A simple container that converts the JSON data received from a command loop
    POST request to an object using dot notation

    Attributes
    ----------
    topic : str
        A category of data being sent, its purpose is similar to a MQTT topic
    payload : Any
        The raw data to be sent for processing
    id : int
        The ID number for this particular message, used for matching an EventLoopMessage
        to the correct EventLoopResponse
    """

    def __init__(self, input_json: Dict[str, Any]):
        self.topic = input_json['topic']
        self.payload = input_json['payload']
        self.id = input_json['id']


class EventLoopResponse:
    """A container for data that is sent as a response to an EventLoopMessage
    from the web app's javascript

    Attributes
    ----------
    topic : str
        A category of data being sent, its purpose is similar to a MQTT topic
    payload : Any
        The raw data to be sent for processing
    id : int
        The ID number for this particular message, used for matching an EventLoopMessage
        to the correct EventLoopResponse
    event : threading.Event
        Blocks until a response is received from the web app's javascript
    result : Optional[Any]
        Data received as a result of the web app processing this object's payload
    """

    def __init__(self, command: Command, *, should_respond: bool):
        """
        Parameters
        ----------
        command : Command
            The Command to convert into an EventLoopResponse
        should_respond : bool
            If the web app should send data back in response to the Command
        """

        self.topic = command.topic
        self.payload = command.payload
        self.should_respond = should_respond

        self.id = id(self)
        self.event = Event()
        self.result: Optional[Any] = None

    def to_json(self) -> Dict[str, Any]:
        """Converts the structured data in this object to JSON

        This data is then sent to the web app's javascript as a response to
        a POST request

        Returns
        -------
        Dict[str, Any]
            The JSON representation of this object, ready for sending to the web app's
            javascript
        """

        return {
            'topic': self.topic,
            'id': self.id,
            'should_respond': self.should_respond,
            'payload': self.payload
        }

    @classmethod
    def nothing(cls):
        """The default EventLoopResponse, used for a generic response"""

        return cls(Command('nothing', None), should_respond=False)


class Postman:
    """A class that manages the sending and receiving of messages and
    their corresponding responses

    Attributes
    ----------
    outgoing_packet_queue : queue.Queue
        A queue that contains EventLoopMessages to be sent to the web app's
        javascript
    in-waiting : Dict[int, EventLoopResponse]
        Maps the message's id and the actual message so that a response can
        be easily matched to its corresponding message
    """

    def __init__(self):
        self.outgoing_packet_queue = Queue()  # packets are just python dicts
        self.in_waiting: Dict[int, EventLoopResponse] = {}

    def invalidate_outgoing_packets(self):
        """Clears the outgoing packet queue so no old messages are sent to the web
        app's javascript
        """

        self.outgoing_packet_queue = Queue()

    def send_and_receive(self, command: Command) -> Any:
        """Sends an EventLoopResponse to the web app's javascript and then waits
        for and returns the resulting data

        Parameters
        ----------
        command : Command
            The Command to be converted into an EventLoopResponse and then
            processed by the web app's javascript

        Returns
        -------
        Any
            The data received as a result of the web app's javascript processing
            the Command
        """

        response = EventLoopResponse(command, should_respond=True)
        self.outgoing_packet_queue.put(response.to_json())
        self.in_waiting[response.id] = response
        response.event.wait()
        return response.result

    def send(self, command: Command):
        """Sends an EventLoopResponse to the web app's javascript but does not wait
        for a response
        """
        response = EventLoopResponse(command, should_respond=False)
        self.outgoing_packet_queue.put(response.to_json())

    def process_message(self, message: EventLoopMessage):
        """Matches an incoming EventLoopMessage from the web app's javascript to its
        corresponding EventLoopResponse and then sets the response's event

        Parameters
        ----------
        message : EventLoopMessage
            A message received from the web app's javascript containing the result
            of a subsequent Command
        """

        match = self.in_waiting[message.id]
        match.result = message.payload
        match.event.set()

    def get_new_packet(self) -> Dict[str, Any]:
        """Returns the next packet in the queue ready for sending or an empty
        packet if there is none

        Returns
        -------
        Dict[str, Any]
            The next packet in the queue ready for sending or an empty packet
            if there is none
        """

        try:
            return self.outgoing_packet_queue.get_nowait()
        except Empty:
            return EventLoopResponse.nothing().to_json()

    def send_buffer_packets(self, num_packets: int):
        """Sends an input amount of empty packets, which acts as a buffer

        Parameters
        ----------
        num_packets : int
            The number of buffer packets to send
        """

        for i in range(num_packets):
            self.outgoing_packet_queue.put(EventLoopResponse.nothing().to_json())


class Page:
    """An object representing one page of the web app

    Attributes
    ----------
    template : str
        The path to the HTML template file to be displayed on the page
    template_kwargs : Dict[str, str]
        Variables to pass into the template to be processed by Jinja
    on_load : Callable[[], None]
        A function that will be called whenever the page loads or reloads
    _update_interval : int
        How often the page should check for a new command (in millis)
    _has_loaded_event : threading.Event
        An event object that will be set once the page is loaded and cleared
        when the page is reloaded
    _postman : Postman
        A class that manages the sending and receiving of messages and
        their corresponding responses
    _callbacks : Dict[str, Callable[[], None]]
        Contains a mapping of an id to a callback to run when a web element
        with that id is clicked or triggered
    """

    def __init__(self, template: str, **template_kwargs):
        """
        Parameters
        ----------
        template : str
            The path to the HTML template file to be displayed on the page
        **template_kwargs
            Variables to pass into the template to be processed by Jinja
        """

        self.template = template
        self.template_kwargs = template_kwargs

        self.on_load: Callable[[], None] = lambda: None
        self._update_interval = 100
        self._has_loaded_event = Event()

        self._postman = Postman()

        self._callbacks: Dict[str, Callable[[], None]] = {}

    def on_request(self) -> Union[Dict[str, Any], str]:
        """Called when the page receives an HTTP request

        A GET request will return the HTML of the page based on its
        template attribute

        A POST request signifies a command loop action, either a request
        for a new message or a response to a previous message

        This method **SHOULD NOT** be overridden

        Returns
        -------
        Dict[str, Any]
            This is returned if the page receives a POST request; a python dict
            that will be converted into a JSON object by the web app's javascript
        str
            This is returned if the page receives a GET request; the raw HTML of
            the page
        """

        if request.method == 'POST':
            # a POST request means the page has fully loaded
            if not self._has_loaded_event.is_set():
                self._has_loaded_event.set()
                self.on_load()

            message = EventLoopMessage(request.json)

            if message.topic == 'command_loop':
                # the page is ready to receive a packet
                return self._postman.get_new_packet()
            elif message.topic == 'onclick':
                # a button was clicked, check if a callback was registered
                if message.payload['id'] in self._callbacks.keys():
                    self._callbacks[message.payload['id']]()
            else:
                self._postman.process_message(message)

            return EventLoopResponse.nothing().to_json()
        else:  # GET request
            # a GET request means the page has been reloaded
            self._has_loaded_event.clear()
            self._postman.invalidate_outgoing_packets()
            self._postman.send_buffer_packets(2)
            # ^ Added to address a bug where some packets are not received by the web app's javascript when reloading
            self.update_interval = self._update_interval
            return render_template(self.template, **self.template_kwargs)

    def wait_for_page_load(self):
        """Blocks until the page is fully loaded"""

        self._has_loaded_event.wait()

    def evaluate_javascript(self, js: str, *, get_output: bool) -> Optional[str]:
        """Evaluates a string of javascript in the web app and returns its result
        if `get_output` is `True`

        Parameters
        ----------
        js : str
            The javascript to execute
        get_output : bool
            Whether to wait for and return the output of executing the javascript
        """

        if get_output:
            return self._postman.send_and_receive(Command.javascript(js))
        else:
            return self._postman.send(Command.javascript(js))

    def set_text(self, tag_id: str, text: str):
        """Sets the `innerHTML` attribute of any tag in the web app

        Parameters
        ----------
        tag_id : str
            The id of the tag to edit

            Example: `<p id="sample_id"></p>`
        text : str
            The text to display in the tag
        """

        text = text.replace('\n', '<br />')  # newlines raise an invalid token error
        js = f'document.getElementById("{tag_id}").innerHTML = "{text}";'
        self.evaluate_javascript(js, get_output=False)

    def set_button_text(self, tag_id: str, text: str):
        """Sets a button's `value` attribute to `text`

        Parameters
        ----------
        tag_id : str
            The id of the button to edit

            Example: `<input type="button" id="sample_id">`
        text : str
            The text to display in the button
        """

        text = text.replace('\n', '<br />')  # newlines raise an invalid token error
        js = f'document.getElementById("{tag_id}").value = "{text}";'
        self.evaluate_javascript(js, get_output=False)

    def console_log(self, text: str):
        """Logs text to the web app's console

        Parameters
        ----------
        text : str
            The text to log to the console
        """

        js = f'console.log("{text}");'
        self.evaluate_javascript(js, get_output=False)

    def set_image_src(self, tag_id: str, src: str):
        """Sets the `src` attribute of an image tag

        Parameters
        ----------
        tag_id : str
            The id of the `<img>` tag to edit

            Example: `<img src="#" id="sample_id"/>`
        src : str
            The string to set the `src` attribute to
        """

        js = f'document.getElementById("{tag_id}").src = "{src}";'
        self.evaluate_javascript(js, get_output=False)

    def set_image_base64(self, tag_id: str, base64_str: str, filetype: str = 'jpg'):
        """Sets an image to a base64-encoded string

        Parameters
        ----------
        tag_id : str
            The id of the `<img>` tag to edit

            Example: `<img src="#" id="sample_id"/>`
        base64_str : str
            The base64-encoded string to set the image to
        filetype : str, default='jpg'
            What filetype the base64-encoded string was encoded from
        """

        self.set_image_src(tag_id,
                           f'data:image/{filetype};base64, {base64_str}')

    def get_input_data(self, tag_id: str) -> str:
        """Returns the data contained in an `<input>` tag in the web app

        Parameters
        ----------
        tag_id : str
            The id of the `<input>` tag to get data from

            Example: `<input type="text" id="sample_id">`

        Returns
        -------
        str
            The data received from the `<input>` tag
        """

        js = f'document.getElementById("{tag_id}").value;'
        return self.evaluate_javascript(js, get_output=True)

    def on_button_click(self, tag_id: str, callback: Callable[[], None]):
        """Registers a callback with a button's id to be called whenever the
        associated button is clicked

        Parameters
        ----------
        tag_id : str
            The id of the `<input type="button">` to listen for
        callback : Callable[[], None]
            The callback to call when the button is clicked
        """

        self._callbacks[tag_id] = callback

    @property
    def update_interval(self) -> int:
        """How often the page should check for a new command (in millis)"""

        return self._update_interval

    @update_interval.setter
    def update_interval(self, interval: int):
        """Set how often (in milliseconds) the command loop should check for a
        new command

        Parameters
        ----------
        interval : int
            How often the page should check for a new command (in millis)
        """

        self._update_interval = interval
        self._postman.send(Command.update_interval(interval))


class EdgeInterface:
    """An interface that manages and runs the web app

    This interface handles both the creation of pages and running the development
    web server

    Attributes
    ----------
    app : Flask
        The actual web app
    pages: Dict[Page]
        A mapping of pages to their respective subdirectory, used for quickly
        gaining access to the page object for page manipulation
    server : threading.Thread
        A background thread that handles serving the web app
    running: bool
        If the server is currently running
    """

    def __init__(self, import_name: str, disable_request_logging: bool = True):
        """
        Parameters
        ----------
        import_name : str
            See Flask API documentation
        disable_request_logging : bool, default=True
            Disable the request logging or not, as it can become a lot due to
            the constant requests from the command loop
        """

        if disable_request_logging:
            log = logging.getLogger('werkzeug')
            log.setLevel(logging.ERROR)

        self.app = Flask(import_name)
        self.pages: Dict[str, Page] = {}

        server_kwargs = {'host': '0.0.0.0', 'port': 5000, 'debug': False}
        self.server = Thread(target=self.app.run, kwargs=server_kwargs)

    @property
    def running(self) -> bool:
        """If the server is currently running

        Returns
        -------
        bool
            If the server is currently running
        """

        return self.server.is_alive()

    def start_server(self):
        """Start serving the web app"""

        if '/' not in self.pages.keys():
            raise MissingMainPage()

        self.server.start()
        self.pages['/'].wait_for_page_load()

    def wait_forever(self):
        """Blocks infinitely, allowing the server to continue running"""

        self.server.join()

    def add_page(self, subdirectory: str, template: str, **template_kwargs):
        """Add a page to the web app

        To access an added page, use the `pages` attribute of this object

        A page with the subdirectory `'/'` is required for the web app to run

        Parameters
        ----------
        subdirectory : str
            The subdirectory of the page to be added

            Example: `https://link.com/page` <- `/page` is the subdirectory
        template : str
            The path to the HTML template file to be displayed on the page
        **template_kwargs
            Variables to pass into the template to be processed by Jinja
        """

        if subdirectory in self.pages.keys():
            raise PageAlreadyExists()

        page = Page(template, **template_kwargs)
        self.app.add_url_rule(subdirectory,
                              str(id(page)),
                              page.on_request,
                              methods=['GET', 'POST'])
        self.pages[subdirectory] = page

    def set_global_update_interval(self, interval: int):
        """Sets how often (in milliseconds) the command loop should check for a
        new command for all pages

        Parameters
        ----------
        interval : int
            How often the page should check for a new command (in millis)
        """

        for page in self.pages.values():
            page.update_interval = interval
