import base64
import functools
import io
import itertools
from collections import namedtuple
from dataclasses import dataclass, field
from typing import List, Tuple

import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

# plot properties
ax = plt.axes(projection='3d')
ax.set_xlabel('x')
ax.set_ylabel('y')
ax.set_zlabel('z')
ax.set_xlim3d(-600, 600)
ax.set_ylim3d(-600, 600)
ax.set_zlim3d(0, 900)

Vector3D = namedtuple('Vector3D', 'x y z')


@dataclass
class PlotConfig:
    line_color: str = 'black'
    enable_tool_history: bool = False
    tool_history_color: str = 'blue'
    enable_axis_lines: bool = False
    axis_line_length: int = 50
    enable_tool_position: bool = True


@dataclass
class ToolHistory:
    xs: List[float] = field(default_factory=list)
    ys: List[float] = field(default_factory=list)
    zs: List[float] = field(default_factory=list)

    def clear(self):
        self.xs.clear()
        self.ys.clear()
        self.zs.clear()

    def add_coord(self, x: float, y: float, z: float):
        self.xs.append(x)
        self.ys.append(y)
        self.zs.append(z)

    def to_scatter(self) -> Tuple[List[float], List[float], List[float]]:
        return self.xs, self.ys, self.zs


class Robot:
    def __init__(self, link_lengths: Tuple[int, ...], num_joints: int):
        self._link_lengths = link_lengths
        self._joint_angles = [0] * num_joints
        self.plot_config = PlotConfig()
        self._tool_history = ToolHistory()

    @property
    def joint_angles(self):
        return self._joint_angles

    @joint_angles.setter
    def joint_angles(self, value):
        if not isinstance(value, list):
            raise ValueError(f'joint_angles must be a list, not {type(value)}')
        elif len(value) != len(self._joint_angles):
            raise ValueError(f'joint_angles must have {len(self._joint_angles)} values, not {len(value)}')
        self._joint_angles = value

    @property
    def d_h_table(self):
        # should be overridden by subclasses
        raise NotImplementedError

    def clear_tool_history(self):
        self._tool_history.clear()

    def get_fk_frames(self):
        d_h_table = self.d_h_table
        frames = []
        for i in range(len(d_h_table)):
            frames.append(
                np.array(
                    [[
                        np.cos(d_h_table[i, 0]),
                        -np.sin(d_h_table[i, 0]) * np.cos(d_h_table[i, 1]),
                        np.sin(d_h_table[i, 0]) * np.sin(d_h_table[i, 1]),
                        d_h_table[i, 2] * np.cos(d_h_table[i, 0])
                    ],
                        [
                            np.sin(d_h_table[i, 0]),
                            np.cos(d_h_table[i, 0]) * np.cos(d_h_table[i, 1]),
                            -np.cos(d_h_table[i, 0]) * np.sin(d_h_table[i, 1]),
                            d_h_table[i, 2] * np.sin(d_h_table[i, 0])
                        ],
                        [
                            0,
                            np.sin(d_h_table[i, 1]),
                            np.cos(d_h_table[i, 1]), d_h_table[i, 3]
                        ], [0, 0, 0, 1]]))

        return frames

    def get_accumulated_frames(self):
        frames = self.get_fk_frames()
        base = np.identity(4)
        return itertools.accumulate(frames, np.matmul, initial=base)

    def get_plot(self) -> bytes:
        ax.lines.clear()
        ax.texts.clear()  # clearing the lists is faster than cla()

        # organize the coords
        xs = []
        ys = []
        zs = []
        for current in self.get_accumulated_frames():
            xs.append(current[0, 3])
            ys.append(current[1, 3])
            zs.append(current[2, 3])

        # dots showing tool history
        if self.plot_config.enable_tool_history:
            self._tool_history.add_coord(xs[-1], ys[-1], zs[-1])
            ax.plot3D(*self._tool_history.to_scatter(), ls=':', color=self.plot_config.tool_history_color)

        # x, y, z lines
        if self.plot_config.enable_axis_lines:
            axll = self.plot_config.axis_line_length
            for frame, x, y, z in zip(self.get_accumulated_frames(), xs, ys, zs):
                ax.plot3D([x, x + frame[0, 0] * axll], [y, y + frame[1, 0] * axll], [z, z + frame[2, 0] * axll],
                          color='red')
                ax.plot3D([x, x + frame[0, 1] * axll], [y, y + frame[1, 1] * axll], [z, z + frame[2, 1] * axll],
                          color='green')
                ax.plot3D([x, x + frame[0, 2] * axll], [y, y + frame[1, 2] * axll], [z, z + frame[2, 2] * axll],
                          color='blue')

        # actual robot path
        ax.plot3D(xs, ys, zs, color=self.plot_config.line_color)

        # format and display the tool position
        if self.plot_config.enable_tool_position:
            coords = f'({round(xs[-1])}, {round(ys[-1])}, {round(zs[-1])})'
            ax.text2D(0.05, 0.95, f"Tool position: {coords}", transform=ax.transAxes)

        # save plot as bytes
        with io.BytesIO() as plt_bytes:
            plt.savefig(plt_bytes, format='jpg')
            plt_bytes.seek(0)
            return plt_bytes.read()

    def get_base64_plot(self) -> str:
        return base64.b64encode(self.get_plot()).decode()

    def save_plot(self, filename: str):
        with io.BytesIO(self.get_plot()) as data:
            image = Image.open(data)
            image.save(filename + '.jpg')

    def get_transformation(self, transformation_num: int):
        return functools.reduce(lambda a, b: a @ b, self.get_fk_frames()[:transformation_num])

    @staticmethod
    def get_velocity(start_pos: Vector3D, end_pos: Vector3D, t: float) -> Vector3D:
        dx = end_pos.x - start_pos.x
        dy = end_pos.y - start_pos.y
        dz = end_pos.z - start_pos.z
        # d = math.sqrt(dx**2 + dy**2 + dz**2)
        return Vector3D(dx / t, dy / t, dz / t)


class WhiteRobot(Robot):
    def __init__(self):
        # Link lengths in millimeters
        a1 = 275  # Length of link 1
        a2 = 190  # Length of link 2
        a3 = 700  # Length of link 3
        a4 = 190  # Length of link 4
        a5 = 500  # Length of link 5
        a6 = 162  # Length of link 6
        a7 = 210  # Length of link 7 + gripper

        super().__init__((a1, a2, a3, a4, a5, a6, a7), 6)

    @property
    def d_h_table(self):
        a1, a2, a3, a4, a5, a6, a7 = self._link_lengths
        theta_1, theta_2, theta_3, theta_4, theta_5, theta_6 = self.joint_angles
        return np.array([[np.deg2rad(theta_1),
                          np.deg2rad(90), 0, a1],
                         [np.deg2rad(theta_2 - 90),
                          np.deg2rad(180), -a3, a2],
                         [np.deg2rad(theta_3 - 90),
                          np.deg2rad(-90), 0, a4],
                         [np.deg2rad(theta_4),
                          np.deg2rad(90), 0, a5],
                         [np.deg2rad(theta_5),
                          np.deg2rad(-90), 0, a6],
                         [np.deg2rad(theta_6), 0, 0, a7]])


class BlueRobot(Robot):
    def __init__(self):
        # Link lengths in millimeters
        a1 = 330  # Length of link 1
        a2 = 40  # Length of link 2
        a3 = 385  # Length of link 3
        a4_5 = 340  # Length of link 4 and 5
        a6 = 160  # Length of link 6 + gripper tip

        super().__init__((a1, a2, a3, a4_5, a6), 6)

    @property
    def d_h_table(self):
        a1, a2, a3, a4_5, a6 = self._link_lengths
        theta_1, theta_2, theta_3, theta_4, theta_5, theta_6 = self.joint_angles
        return np.array([[np.deg2rad(theta_1 + 90),
                          np.deg2rad(90), a2, a1],
                         [np.deg2rad(theta_2 + 90), 0, a3, 0],
                         [np.deg2rad(theta_3),
                          np.deg2rad(90), 0, 0],
                         [np.deg2rad(theta_4),
                          np.deg2rad(-90), 0, a4_5],
                         [np.deg2rad(theta_5),
                          np.deg2rad(90), 0, 0],
                         [np.deg2rad(theta_6), 0, 0, a6]])


if __name__ == '__main__':
    R01 = WhiteRobot()
    R02 = BlueRobot()
    R03 = BlueRobot()
