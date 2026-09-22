import os
import time

from color_cli import color_text
from card_sdk.card import base_dir
from super_progress_bar import Progress
import pywriter as tw

import ui

progress = Progress(
    min_value=0,
    max_value=100,
    unit="items",
    colors=[(0, 0, 0), (255, 255, 0), (255, 255, 0),(0, 0, 0)],
    single_color=False
)



def clean():
    # Avoid spawning a shell subprocess (`clear`/`cls`) on every single card —
    # that was a real per-card cost when called from parallel workers. Use the
    # ANSI clear screen codes directly; they are instant and portable.
    import sys
    if os.name == "nt":
        sys.stdout.write("\x1b[2J\x1b[H")
    else:
        sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


class Tkd:

    def cleaner():
        clean()
        


    def hello():

        print(color_text("""
                █▀▀ ▄▀█ █▀█ █▀▄   █▀ █▀▄ █▄▀
                █▄▄ █▀█ █▀▄ █▄▀   ▄█ █▄▀ █░█⠀version 1.0⠀""","yellow"))

        print(color_text("\n Welcome, 𝓴𝓪𝓪𝓼𝓬𝓪𝓷 𝓼𝓭𝓴 \n","green"))
        print(color_text("This is a kaascan tool used for generating students cards. \nvia their integrated softwares or cli this tool is deployed \nunder mit licence as it is opensource.\n","white"))


    def checking_task_progress(process: str,current_progress: float):

        print(color_text(f"[>] {process}...\n","white"))
        progress.update(current_progress)
        print("\n")
        Tkd.cleaner()
        Tkd.hello()
        
     
        
    
    def ask_user(question: str):

        result = ui.ask_yes_no(color_text(question,"blue"))
        return result

    def inform_user(info: str):
        Tkd.cleaner()
        ui.info(ui.green,"[::]",info)
        
   
     
        
      





# Tkd.hello()
# Tkd.checking_task_progress("Baking cards",10)
# # print(Tkd.ask_user("How old are you ?"))
# Tkd.inform_user("Hello, world")