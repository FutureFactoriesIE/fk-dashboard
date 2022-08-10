import ast
import itertools
import time
from enum import Enum
from functools import partial
from threading import Thread
from typing import Optional

import matplotlib

from edge_interface import EdgeInterface

matplotlib.use('Agg')  # set backend before importing robots

from robots import WhiteRobot, BlueRobot

robots = [WhiteRobot(), BlueRobot(), BlueRobot()]
actives = [False, False, False]
default_plots = [robot.get_base64_plot() for robot in robots]

interface = EdgeInterface(__name__)


class ToggleID(Enum):
    T_ACTIVE = 't_active'
    T_HISTORY = 't_history'
    T_COORDS = 't_coords'
    T_AXIS = 't_axis'


def force_replot(specific_index: Optional[int] = None):
    global old_data
    if specific_index is None:
        old_data = [[-1.0] * 6] * len(robots)
    else:
        old_data[specific_index] = [-1.0] * 6


def on_load():
    # set update interval button text
    interface.pages['/'].set_button_text('update_interval_button', f'{interface.pages["/"].update_interval}ms')

    # show default plots
    for i, robot in enumerate(robots):
        if not actives[i]:
            interface.pages['/'].set_image_base64(f'r0{i + 1}_plot', default_plots[i])
    force_replot()  # replot robots that are active

    # set active buttons
    js = []
    for i, robot in enumerate(robots):
        for t_id in ToggleID:
            add_remove = 'remove'
            if t_id == ToggleID.T_ACTIVE:
                add_remove = 'add' if actives[i] else 'remove'
            elif t_id == ToggleID.T_HISTORY:
                add_remove = 'add' if robot.plot_config.enable_tool_history else 'remove'
            elif t_id == ToggleID.T_COORDS:
                add_remove = 'add' if robot.plot_config.enable_tool_position else 'remove'
            elif t_id == ToggleID.T_AXIS:
                add_remove = 'add' if robot.plot_config.enable_axis_lines else 'remove'

            js.append(f'document.getElementById("r0{i + 1}_{t_id.value}").classList.{add_remove}("active");')
    interface.pages['/'].evaluate_javascript(' '.join(js), get_output=False)


def on_toggle_click(robot_index: int, t_id: ToggleID):
    robot = robots[robot_index]
    if t_id == ToggleID.T_ACTIVE:
        actives[robot_index] = not actives[robot_index]
    elif t_id == ToggleID.T_HISTORY:
        robot.clear_tool_history()
        robot.plot_config.enable_tool_history = not robot.plot_config.enable_tool_history
    elif t_id == ToggleID.T_COORDS:
        robot.plot_config.enable_tool_position = not robot.plot_config.enable_tool_position
    elif t_id == ToggleID.T_AXIS:
        robot.plot_config.enable_axis_lines = not robot.plot_config.enable_axis_lines

    force_replot(robot_index)


intervals = itertools.cycle([75, 50, 25, 10, 100])


def on_update_interval_click():
    new_interval = next(intervals)
    interface.pages['/'].update_interval = new_interval
    interface.pages['/'].set_button_text('update_interval_button', f'{interface.pages["/"].update_interval}ms')


def on_secret_party_mode_button_click():
    for robot in robots:
        robot.plot_config.line_color = 'black' if robot.plot_config.line_color is None else None


class DummyData(Thread):
    def __init__(self):
        Thread.__init__(self)
        with open('data4-smoothed') as f:
            data = ast.literal_eval(f.read())

        self.joint_angles = itertools.cycle(data)
        self.current = [[0] * 6] * 3

    def run(self) -> None:
        while True:
            self.current = [next(self.joint_angles)] * 3
            time.sleep(0.25)


if __name__ == '__main__':
    interface.add_page('/', 'index.html')
    interface.pages['/'].on_load = on_load
    interface.start_server()

    # register toggle button callbacks
    for i in range(len(robots)):
        for t_id in ToggleID:
            interface.pages['/'].on_button_click(f'r0{i + 1}_{t_id.value}', partial(on_toggle_click, i, t_id))

    interface.pages['/'].on_button_click('update_interval_button', on_update_interval_click)
    interface.pages['/'].on_button_click('logo', on_secret_party_mode_button_click)

    ddata = DummyData()
    ddata.start()

    old_data = [[0.0] * 6] * len(robots)

    while True:
        for i, robot in enumerate(robots):
            if actives[i] and old_data[i] != ddata.current[i]:
                old_data[i] = ddata.current[i]
                robot.joint_angles = ddata.current[i]
                interface.pages['/'].set_image_base64(f'r0{i + 1}_plot', robot.get_base64_plot())
                text = [f'M_R0{i + 1}_{joint_letter}: {round(ddata.current[i][j], 2)}' for j, joint_letter in
                        enumerate(('S', 'L', 'U', 'R', 'B', 'T'))]
                interface.pages['/'].set_text(f'r0{i + 1}_data', '\n'.join(text))
