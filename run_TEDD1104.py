from model import Tedd1104ModelPL
from keyboard.getkeys import key_check
import argparse
from screen.screen_recorder import ImageSequencer
import torch
import logging
import time
from tkinter import *
import numpy as np
import cv2
from torchvision import transforms
from utils import mse
from controller.xbox_controller_emulator import XboxControllerEmulator
from keyboard.inputsHandler import select_key
from keyboard.getkeys import key_press
from utils import IOHandler
import os
import glob

if torch.cuda.is_available():
    device = torch.device("cuda:0")
else:
    device = torch.device("cpu")
    logging.warning("GPU not found, using CPU, inference will be very slow.")


def run_ted1104(
    model_dir,
    enable_evasion: bool,
    show_current_control: bool,
    num_parallel_sequences: int = 2,
    width: int = 1600,
    height: int = 900,
    full_screen: bool = False,
    evasion_score=1000,
    control_mode: str = "keyboard",
) -> None:
    """
    Generate dataset exampled from a human playing a videogame
    HOWTO:
        Set your game in windowed mode
        Set your game to 1600x900 resolution
        Move the game window to the top left corner, there should be a blue line of 1 pixel in the left bezel of your
         screen and the window top bar should start in the top bezel of your screen.
        Let the AI play the game!
    Controls:
        Push QE to exit
        Push L to see the input images
        Push and hold J to use to use manual control

    Input:
    - model_dir: Directory where the model to use is stored (model.bin and model_hyperparameters.json files)
    - enable_evasion: automatic evasion maneuvers when the car gets stuck somewhere. Note: It adds computation time
    - show_current_control: Show a window with text that indicates if the car is currently being driven by
      the AI or a human
    - num_parallel_sequences: num_parallel_sequences to record, is the number is larger the recorded sequence of images
      will be updated faster and the model  will use more recent images as well as being able to do more iterations
      per second. However if num_parallel_sequences is too high it wont be able to update the sequences with 1/10 secs
      between images (default capturerate to generate training examples).
    - width: Game window width
    - height: Game window height
    - full_screen: If you are playing in full screen (no window border on top) enable this
    -evasion_score: Mean squared error value between images to activate the evasion maneuvers
    -enable_segmentation: Image segmentation will be performed using a pretrained model. Cars, persons, bikes.. will be
     highlighted to help the model to identify them.

    Output:

    """

    assert control_mode in [
        "keyboard",
        "controller",
    ], f"{control_mode} control mode not supported. Supported dataset types: [keyboard, controller].  "

    show_what_ai_sees: bool = False
    fp16: bool

    model_path = glob.glob(os.path.join(model_dir, "checkpoints/*.ckpt"))
    if len(model_path) > 1:
        print(
            f"WARNING!!! We found multiple checkpoints in the directory, we will load the last one: {model_path}"
        )

    model_path = model_path[0]

    hparams_path = os.path.join(model_dir, "hparams.yaml")

    model = Tedd1104ModelPL.load_from_checkpoint(
        checkpoint_path=model_path, hparams_file=hparams_path
    )

    model.eval()
    model.to(device)

    model_output_mode: str = model.control_mode

    io_handler: IOHandler = IOHandler()

    if control_mode == "controller":
        xbox_controller: XboxControllerEmulator = XboxControllerEmulator()

    transform = transforms.Compose(
        [
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
    )

    img_sequencer = ImageSequencer(
        width=width,
        height=height,
        full_screen=full_screen,
        get_controller_input=False,
        num_sequences=num_parallel_sequences,
        total_wait_secs=5,
    )

    if show_current_control:
        root = Tk()
        var = StringVar()
        var.set("T.E.D.D. 1104 Driving")
        text_label = Label(root, textvariable=var, fg="green", font=("Courier", 44))
        text_label.pack()
    else:
        root = None
        var = None
        text_label = None

    last_time: float = time.time()
    score: np.float = np.float(0)
    last_num: int = 5  # The image sequence starts with images containing zeros, wait until it is filled

    close_app: bool = False
    model_prediction = np.zeros(3 if model_output_mode == "controller" else 1)

    while not close_app:
        try:
            while last_num == img_sequencer.actual_sequence:
                time.sleep(0.01)

            last_num = img_sequencer.actual_sequence
            img_seq, _ = img_sequencer.get_sequence()

            init_copy_time: float = time.time()

            keys = key_check()
            if "J" not in keys:

                x: torch.tensor = torch.stack(
                    (
                        transform(img_seq[0] / 255.0),
                        transform(img_seq[1] / 255.0),
                        transform(img_seq[2] / 255.0),
                        transform(img_seq[3] / 255.0),
                        transform(img_seq[4] / 255.0),
                    ),
                    dim=0,
                ).to(device=device, dtype=torch.float)

                with torch.no_grad():
                    model_prediction: torch.tensor = model(x)[0].cpu().numpy()

                model_prediction = io_handler.input_conversion(
                    input_value=model_prediction,
                    output_type=control_mode,
                )

                if control_mode == "controller":
                    xbox_controller.set_controller_state(
                        lx=model_prediction[0],
                        lt=model_prediction[1],
                        rt=model_prediction[2],
                    )
                else:
                    select_key(model_prediction)

                key_push_time: float = time.time()

                if show_current_control:
                    var.set("T.E.D.D. 1104 Driving")
                    text_label.config(fg="green")
                    root.update()

                if enable_evasion:
                    score = mse(img_seq[0], img_seq[4])
                    if score < evasion_score:
                        if show_current_control:
                            var.set("Evasion maneuver")
                            text_label.config(fg="blue")
                            root.update()
                        if control_mode == "controller":
                            xbox_controller.set_controller_state(lx=0, lt=1.0, rt=-1.0)
                            time.sleep(1)
                            if np.random.rand() > 0.5:
                                xbox_controller.set_controller_state(
                                    lx=1.0, lt=0.0, rt=-1.0
                                )
                            else:
                                xbox_controller.set_controller_state(
                                    lx=-1.0, lt=0.0, rt=-1.0
                                )
                            time.sleep(0.2)
                        else:
                            select_key(4)
                            time.sleep(1)
                            if np.random.rand() > 0.5:
                                select_key(6)
                            else:
                                select_key(8)
                            time.sleep(0.2)

                        if show_current_control:
                            var.set("T.E.D.D. 1104 Driving")
                            text_label.config(fg="green")
                            root.update()

            else:
                if show_current_control:
                    var.set("Manual Control")
                    text_label.config(fg="red")
                    root.update()

                if control_mode == "controller":
                    xbox_controller.set_controller_state(lx=0.0, lt=-1, rt=-1.0)

                key_push_time: float = 0.0

            if show_what_ai_sees:
                cv2.imshow("window1", img_seq[0])
                cv2.waitKey(1)
                cv2.imshow("window2", img_seq[1])
                cv2.waitKey(1)
                cv2.imshow("window3", img_seq[2])
                cv2.waitKey(1)
                cv2.imshow("window4", img_seq[3])
                cv2.waitKey(1)
                cv2.imshow("window5", img_seq[4])
                cv2.waitKey(1)

            if "L" in keys:
                time.sleep(0.1)  # Wait for key release
                if show_what_ai_sees:
                    cv2.destroyAllWindows()
                    show_what_ai_sees = False
                else:
                    show_what_ai_sees = True

            time_it: float = time.time() - last_time

            if control_mode == "controller":
                info_message = f"LX: {int(model_prediction[0]*100)}%\n"
                f"LT: {int(model_prediction[1]*100)}%\n"
                f"RT: {int(model_prediction[2]*100)}%"
            else:
                info_message = f"Predicted Key: {key_press(model_prediction)}"

            print(
                f"Recording at {img_sequencer.screen_recorder.fps} FPS\n"
                f"Actions per second {None if time_it==0 else 1/time_it}\n"
                f"Reaction time: {round(key_push_time-init_copy_time,3) if key_push_time>0 else 0} secs\n"
                f"{info_message}\n"
                f"Difference from img 1 to img 5 {None if not enable_evasion else score}\n"
                f"Push Ctrl + C to exit\n"
                f"Push L to see the input images\n"
                f"Push J to use to use manual control\n",
                end="\r",
            )

            last_time = time.time()

        except KeyboardInterrupt:
            print()
            img_sequencer.stop()
            if control_mode == "controller":
                xbox_controller.stop()
            close_app = True


if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--model_dir",
        type=str,
        required=True,
        help="Directory where the model to use is stored",
    )

    parser.add_argument("--width", type=int, default=1600, help="Game window width")
    parser.add_argument("--height", type=int, default=900, help="Game window height")

    parser.add_argument(
        "--enable_evasion",
        action="store_true",
        help="Enable automatic evasion maneuvers when the car gets stuck somewhere. Note: It adds computation time",
    )

    parser.add_argument(
        "--show_current_control",
        action="store_true",
        help="Show a window with text that indicates if the car is currently being driven by the AI or a human",
    )

    parser.add_argument(
        "--num_parallel_sequences",
        type=int,
        default=1,
        help="num_parallel_sequences to record, is the number is larger the recorded sequence of images will be "
        "updated faster and the model  will use more recent images as well as being able to do more iterations "
        "per second. However if num_parallel_sequences is too high it wont be able to update the sequences with "
        "1/10 secs between images (default capturete to generate training examples). ",
    )

    parser.add_argument(
        "--evasion_score",
        type=float,
        default=200,
        help="Mean squared error value between images to activate the evasion maneuvers",
    )

    parser.add_argument(
        "--control_mode",
        type=str,
        choices=["keyboard", "controller"],
        default="keyboard",
        help="Set if the dataset true values will be keyboard inputs (9 classes) "
        "or Controller Inputs (2 continuous values)",
    )

    parser.add_argument(
        "--full_screen",
        action="store_true",
        help="full_screen: If you are playing in full screen (no window border on top) set this flag",
    )

    args = parser.parse_args()

    run_ted1104(
        model_dir=args.model_dir,
        width=args.width,
        height=args.height,
        full_screen=args.full_screen,
        enable_evasion=args.enable_evasion,
        show_current_control=args.show_current_control,
        num_parallel_sequences=args.num_parallel_sequences,
        evasion_score=args.evasion_score,
        control_mode=args.control_mode,
    )
