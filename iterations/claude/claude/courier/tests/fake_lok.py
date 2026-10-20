"""Synthetic, LoK-*shaped* loopback MUD for dry runs. Original text; no game source.

ROM-style "\n\r" line endings, ANSI color, IAC WILL/WONT ECHO around password
entry, a MOTD pager, a sign, an NPC, a delayed ambient arrival, and a level-1
quit warning. Records every received line to commands.log, with password lines
replaced by "<password>", so replays can be detected after the fact.
"""
import asyncio
import sys
from pathlib import Path

IAC, WILL, WONT, DO, DONT, SB, SE, ECHO = 255, 251, 252, 253, 254, 250, 240, 1
C = {"x": "\x1b[0m", "title": "\x1b[1;36m", "exits": "\x1b[0;32m", "npc": "\x1b[0;33m",
     "sign": "\x1b[1;37m", "warn": "\x1b[1;31m", "dim": "\x1b[1;30m"}
LOG = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("commands.log")

ROOMS = {
    "hall": {
        "title": "Newbie School: Entrance Hall",
        "desc": ("Lamplight pools across worn flagstones. A wooden sign is nailed beside the\n\r"
                 "arch to the north, and a draft carries the smell of oiled leather from the\n\r"
                 "training yard beyond."),
        "exits": ["north", "south"],
        "occupants": ["A kender in patched blue leggings is idly inspecting your belt pouch."],
    },
    "yard": {
        "title": "Newbie School: Training Yard",
        "desc": "Packed earth, a rack of blunted practice blades, and a battered straw dummy.",
        "exits": ["south"],
        "occupants": ["A practice dummy sags on its post."],
    },
}
SIGN = ("The sign reads:\n\r"
        "  New arrivals: read before you wander. Wear what the school gives you;\n\r"
        "  it will serve you better than anything you find on the road.\n\r"
        "  North lies the training yard. South lies the road, and the road bites.")


def prompt(state):
    return f"\n\r<{state['hp']}hp {state['mana']}m {state['mv']}mv> "


def render_room(key):
    room = ROOMS[key]
    lines = [f"{C['title']}{room['title']}{C['x']}", room["desc"],
             f"{C['exits']}[Exits: {' '.join(room['exits'])}]{C['x']}"]
    lines += [f"{C['npc']}{o}{C['x']}" for o in room["occupants"]]
    return "\n\r".join(lines)


class Player:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.state = {"hp": 20, "mana": 100, "mv": 110, "room": "hall", "quit_warned": False}
        self.name, self.password = None, None

    def out(self, text):
        self.writer.write(text.encode("utf-8"))

    def iac(self, *codes):
        self.writer.write(bytes([IAC, *codes]))

    async def line(self, secret=False):
        buf, state = bytearray(), "text"
        while True:
            data = await self.reader.read(1)
            if not data:
                raise ConnectionError("client left")
            byte = data[0]
            if state == "iac":
                state = "opt" if byte in (WILL, WONT, DO, DONT) else "sub" if byte == SB else "text"
                continue
            if state == "opt":
                state = "text"
                continue
            if state == "sub":
                state = "sub_iac" if byte == IAC else "sub"
                continue
            if state == "sub_iac":
                state = "text" if byte == SE else "sub"
                continue
            if byte == IAC:
                state = "iac"
            elif byte == 10:
                text = bytes(buf).rstrip(b"\r").decode("utf-8", "replace")
                with LOG.open("a", encoding="utf-8") as log:
                    log.write(("<password>" if secret else text) + "\n")
                return text
            else:
                buf.append(byte)

    async def run(self):
        self.out(f"{C['dim']}~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~{C['x']}\n\r"
                 f"{C['title']}   A SYNTHETIC KRYNN-SHAPED TEST WORLD{C['x']}\n\r"
                 f"{C['dim']}   (fixture for MudGPT dry runs, not LoK){C['x']}\n\r"
                 f"{C['dim']}~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~{C['x']}\n\r\n\r"
                 "By what name do you wish to be known? ")
        await self.writer.drain()
        self.name = (await self.line()).strip().capitalize()
        self.out(f"Did I get that right, {self.name} (Y/N)? ")
        if (await self.line()).strip().lower() != "y":
            self.out("Then begone.\n\r")
            return
        self.iac(WILL, ECHO)
        self.out(f"New character.\n\rGive me a password for {self.name}: ")
        first = await self.line(secret=True)
        self.out("\n\rPlease retype password: ")
        second = await self.line(secret=True)
        self.iac(WONT, ECHO)
        if first != second:
            self.out("\n\rPasswords don't match.\n\r")
            return
        self.out(f"\n\r{C['warn']}Remember: roleplay is expected here. Write a description by level 5.{C['x']}\n\r"
                 "Welcome, traveler, to the synthetic world.\n\r[Hit Return to continue]")
        await self.line()
        self.out(render_room("hall") + prompt(self.state))
        asyncio.get_running_loop().call_later(6, self.ambient)
        while True:
            command = (await self.line()).strip()
            self.out(self.handle(command) + prompt(self.state))
            await self.writer.drain()
            if self.state.get("gone"):
                return

    def ambient(self):
        if self.state["room"] == "hall" and not self.state.get("gone"):
            ROOMS["hall"]["occupants"].append("A hooded figure leans against the wall, watching you.")
            self.out(f"\n\r{C['npc']}A hooded figure arrives from the south.{C['x']}" + prompt(self.state))

    def handle(self, command):
        verb, _, arg = command.partition(" ")
        verb = verb.lower()
        room = ROOMS[self.state["room"]]
        if verb in ("l", "look") and not arg:
            return render_room(self.state["room"])
        if verb in ("l", "look") and arg.lower() == "sign" and self.state["room"] == "hall":
            return f"{C['sign']}{SIGN}{C['x']}"
        if verb in ("l", "look"):
            return "You do not see that here."
        if verb in ("n", "north", "s", "south"):
            direction = {"n": "north", "s": "south"}.get(verb, verb)
            if direction not in room["exits"]:
                return "Alas, you cannot go that way."
            if self.state["room"] == "hall" and direction == "south":
                return "A Solamnic guard steps into the arch. 'Not until you've trained, recruit.'"
            self.state["room"] = "yard" if direction == "north" else "hall"
            self.state["mv"] -= 1
            return render_room(self.state["room"])
        if verb == "score":
            return (f"You are {self.name}, a level 1 human necromancer.\n\r"
                    "Str: 13(13)  Int: 18(19)  Wis: 16(17)  Dex: 14(14)  Con: 12(12)\n\r"
                    f"HP: {self.state['hp']}/20  Mana: {self.state['mana']}/100  Move: {self.state['mv']}/110\n\r"
                    "Practices: 5  Trains: 3  Exp to level: 1000")
        if verb in ("eq", "equipment"):
            return "You are using:\n\r<worn on body>     a school-issue tunic (glowing faintly)"
        if verb == "emote" and arg:
            return f"{self.name} {arg}"
        if verb == "say" and arg:
            return f"You say '{arg}'"
        if verb == "consider" and arg:
            return "You would need a very good reason. (Fixture: consider is not modelled.)"
        if verb == "quit":
            if not self.state["quit_warned"]:
                self.state["quit_warned"] = True
                return (f"{C['warn']}You are level 1. Characters below level 2 are NOT saved.\n\r"
                        f"Type quit again if you really mean to leave.{C['x']}")
            self.state["gone"] = True
            return "Fare well. (Your character was not saved.)\n\r"
        return "Huh?"


async def main():
    port = int(sys.argv[1])

    async def handle(reader, writer):
        try:
            await Player(reader, writer).run()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    print("fake LoK listening", server.sockets[0].getsockname()[1], flush=True)
    async with server:
        await server.serve_forever()


asyncio.run(main())
