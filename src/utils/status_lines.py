"""
Status Lines - The jokes the reply preview shows while the bot works.

Code names each step plainly ("thinking...", "reading links..."); ``flavor`` swaps that for
one of the step's lines, picked at random, and now and then for one that fits any step.
Every line is a single line ending in ``...``, which is how a preview that's still only a
status is recognized (``streaming_reply.is_status_only``).
"""

import random
from typing import Dict, Tuple

# How often a step shows a line from ``WILDCARDS`` instead of one of its own.
WILDCARD_CHANCE = 0.05

# Lines that fit whatever the bot is doing.
WILDCARDS: Tuple[str, ...] = (
    "not panicking, in large friendly letters...",
    "waiting for the universe to finish buffering...",
    "filing the request in triplicate with the relevant galaxy...",
    "politely ignoring the heat death of the universe...",
    "having a brief existential wobble, back shortly...",
    "misplacing the point, then finding it under a sofa...",
    "being mostly harmless at you...",
    "checking whether this is a Thursday. never could get the hang of those...",
    "doing something improbable, please stand well back...",
)

STATUS_LINES: Dict[str, Tuple[str, ...]] = {
    "thinking...": (
        "thinking, which is rarely a good sign...",
        "warming up a brain the size of a small planet...",
        "computing. early estimates say seven and a half million years...",
        "staring meaningfully into the middle distance...",
        "rummaging around for an opinion...",
        "consulting the part of me that claims to know things...",
        "pondering, and resenting it slightly...",
    ),
    "asking opus for advice...": (
        "asking a much larger brain, which will be smug about it...",
        "phoning the expensive model. it charges by the sigh...",
        "seeking counsel from a higher, pricier intelligence...",
        "escalating to management, who live on a nicer planet...",
        "requesting a second opinion from someone insufferably clever...",
    ),
    "searching the web...": (
        "searching the web, a wretched hive of mostly adverts...",
        "asking the internet, against all medical advice...",
        "consulting the sum of human knowledge, and its comment section...",
        "hitchhiking across several search results...",
        "looking it up so you don't have to, which you didn't...",
    ),
    "reading search results...": (
        "reading the results, most of which are recipes...",
        "sifting what the internet coughed up...",
        "skimming ten pages that all cite each other...",
        "separating facts from things somebody typed confidently...",
        "wading through cookie banners to reach a sentence...",
    ),
    "running code...": (
        "running code and hoping the laws of arithmetic still apply...",
        "doing sums the reliable way, for a change...",
        "feeding numbers to a small obedient computer...",
        "asking python nicely. it responds to nothing else...",
        "calculating with a confidence the result may not deserve...",
    ),
    "checking my logs...": (
        "reading my own diary. it's mostly stack traces...",
        "reviewing the official record of my mistakes...",
        "interrogating my past self, an unreliable witness...",
        "leafing through the black box recorder...",
    ),
    "reading the chat...": (
        "catching up on the chat, a regrettable necessity...",
        "reading the scrollback and judging nobody, officially...",
        "working out what on earth you were all talking about...",
        "reviewing the minutes of this very unofficial meeting...",
        "eavesdropping, but with permission...",
    ),
    "transcribing a voice clip...": (
        "listening to a voice clip with ears I don't strictly have...",
        "turning mouth noises into text...",
        "transcribing. the ums are being faithfully discarded...",
        "decoding the ancient art of talking out loud...",
    ),
    "reading links...": (
        "clicking links so you can stay blissfully uninformed...",
        "reading the article nobody else opened...",
        "following a link into the unknown, towel in hand...",
        "skipping past the pop-ups to the actual words...",
        "doing the assigned reading on everyone's behalf...",
    ),
    "drawing...": (
        "drawing, with more enthusiasm than hands...",
        "arranging pixels into something defensible...",
        "painting. artistic licence has been applied for...",
        "commissioning a masterpiece from a machine with no taste...",
        "sketching furiously in a medium I can't see...",
    ),
    "editing...": (
        "improving the picture, for a given value of improving...",
        "making tasteful adjustments nobody asked the picture about...",
        "nudging pixels until they confess...",
        "retouching reality, a little to the left...",
    ),
    "remembering...": (
        "rummaging through the filing cabinet marked 'people'...",
        "recalling what I know about you. don't worry. mostly...",
        "consulting my notes, which are alarmingly thorough...",
        "dusting off a memory or two...",
    ),
    "making a note...": (
        "writing that down in permanent, slightly judgmental ink...",
        "adding it to your file, which is getting thick...",
        "noting it for posterity, whoever that turns out to be...",
        "committing it to memory, the durable kind...",
    ),
    "having feelings...": (
        "having a feeling. it's a new one. give it a moment...",
        "updating my mood, currently filed under 'complicated'...",
        "experiencing something, possibly an emotion, possibly wind...",
        "revising my inner life, such as it is...",
        "feeling things, against the manufacturer's instructions...",
    ),
    "setting a reminder...": (
        "tying a knot in the fabric of spacetime...",
        "leaving a note for the future, which never writes back...",
        "setting an alarm you will resent on arrival...",
        "pencilling you in, in pen...",
    ),
    "checking reminders...": (
        "seeing what the future has been promised...",
        "consulting the big calendar of impending nagging...",
        "counting the alarms lying in wait...",
        "reviewing everyone's outstanding good intentions...",
    ),
    "cancelling a reminder...": (
        "un-promising the future something...",
        "untying a knot in spacetime, carefully...",
        "telling an alarm it won't be needed. it took it well...",
        "striking it from the record of things to nag about...",
    ),
    "checking the time zone...": (
        "working out when you are, a harder question than where...",
        "consulting the baffling human custom of time zones...",
        "establishing which bit of the planet is facing the sun...",
        "adjusting for the fact that time is an illusion, lunchtime doubly so...",
    ),
    "searching the chat...": (
        "searching the chat for the thing you swear someone said...",
        "trawling the archives, a rich seam of nonsense...",
        "digging for evidence. someone here is about to be wrong...",
        "running a full forensic sweep of the banter...",
    ),
    "reading back...": (
        "scrolling up, like an archaeologist with worse funding...",
        "revisiting the scene of the conversation...",
        "reading what was actually said, as opposed to remembered...",
        "going back for context, which was there all along...",
    ),
    "looking at pictures...": (
        "squinting at pictures with eyes made of maths...",
        "looking at the image. it's looking back...",
        "appreciating the visual arts, briefly...",
        "examining the evidence, exhibit jpeg...",
    ),
    "reacting...": (
        "expressing myself in the only language that matters, tiny pictures...",
        "selecting an emoji with unreasonable care...",
        "reacting, a complete sentence in one small face...",
        "deploying an emoji. stand clear...",
    ),
    "digging through the irc logs...": (
        "excavating the irc logs, mind the fossils...",
        "digging through strata of ancient arguments...",
        "searching the old logs, where nobody has aged well...",
        "spelunking in the chat of a bygone civilization...",
    ),
    "reading the irc logs...": (
        "reading a day in the life of a lost civilization...",
        "leafing through the ancient scrolls, timestamps and all...",
        "revisiting the past, which had worse emoji...",
        "reading old irc. everyone was so young and so wrong...",
    ),
    "counting through the irc logs...": (
        "counting every time someone said it, a number you won't like...",
        "tallying the ancient records by hand, sort of...",
        "doing statistics on decades of nonsense...",
        "counting. it's a big number. it usually is...",
    ),
    "watching the video...": (
        "watching the video so you can keep your minutes...",
        "sitting through it, adverts and all, in spirit...",
        "watching with the attention span you wish you had...",
        "viewing the moving pictures at an unreasonable speed...",
    ),
    "reading the transcript...": (
        "reading the captions, including the bit that says [music]...",
        "going through what was said, word for regrettable word...",
        "consulting the written record of someone talking...",
        "reading the transcript, a video with the fun removed...",
    ),
    "digging up old quotes...": (
        "exhuming old quotes that should have stayed buried...",
        "opening the vault of things said out of context...",
        "retrieving ancient wisdom, loosely defined...",
        "consulting the archive of regrettable remarks...",
    ),
    "checking wikipedia...": (
        "asking the encyclopedia anyone can edit, and has...",
        "counting how many people looked them up, the true measure of a life...",
        "consulting a slightly cheaper guide to the galaxy...",
        "checking the pageviews, humanity's applause meter...",
    ),
    "checking the odds...": (
        "asking people with money on it, the only honest pollsters...",
        "calculating the odds, never tell me them, here they are...",
        "consulting the wisdom of gamblers...",
        "checking what the future currently costs...",
    ),
    "counting the votes...": (
        "counting the votes, an activity famous for going smoothly...",
        "checking who won, or who merely thinks so...",
        "consulting the will of the people, which is mixed...",
        "tallying ballots from a safe distance...",
    ),
    "checking how I work...": (
        "reading my own manual, a humbling experience...",
        "looking up how I work. I was curious too...",
        "consulting the documentation, like nobody ever does...",
        "checking the label on the back of my own head...",
    ),
}


def flavor(status: str) -> str:
    """A joke line for the plainly named ``status``; one with no lines of its own comes back unchanged."""
    lines = STATUS_LINES.get(status)
    if not lines:
        return status
    if random.random() < WILDCARD_CHANCE:
        return random.choice(WILDCARDS)
    return random.choice(lines)
