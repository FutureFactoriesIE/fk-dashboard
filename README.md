# fk-dashboard
An app that runs on the edge device that displays real-time positions of all of the robots' joints. There are a few toggles that can be clicked to change the way the plots are being displayed. The numbers at the bottom of the plots are the real-time joint angles of each robot.

It works by pulling the actual joint angles of the robots through the IE Databus, then performing forward kinematics on these joint angles, and finally plotting each joint/link.

![screenshot](https://drive.google.com/uc?export=view&id=1Y8s-JsXvcvnxHvyLh4AG-RY3qKMpwARu)
