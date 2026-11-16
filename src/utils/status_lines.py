"""
Status Lines - The jokes the reply preview shows while the bot works.

Code names each step plainly ("thinking...", "reading links..."); ``flavor`` swaps that for
one of the step's lines, picked at random, and now and then for one that fits any step.
The owner can also keep private lines for particular people (``config.get_user_status_lines``),
which that person's mentions sometimes show instead: a running joke that isn't in the repo.

The voice is dry and Hitchhiker's Guide-ish: deadpan, mildly put-upon, cosmic bureaucracy,
understatement. Every step the bot shows needs lines here, and new ones should be jokes too.
Every line is a single line ending in ``...``, which is how a preview that's still only a
status is recognized (``streaming_reply.is_status_only``).
"""

import random
from typing import Dict, Mapping, Optional, Sequence, Tuple

# How often a step shows a line from ``WILDCARDS`` instead of one of its own.
WILDCARD_CHANCE = 0.05
# How often a step shows one of the asker's private lines, when they have any that fit it.
USER_LINE_CHANCE = 0.25
# The key, in a person's private lines, for the ones that fit any step.
ANY_STEP = "*"

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
    "locating my towel. one moment...",
    "consulting the small print of the universe...",
    "this would be quicker with a second head...",
    "waiting on a department that was demolished for a bypass...",
    "experiencing a moment of infinite improbability. normal service soon...",
    "asking the drinks machine for tea. negotiations continue...",
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
        "arriving at an answer. working out the question may take longer...",
        "thinking deep thoughts. shallow ones were out of stock...",
        "here I am, brain the size of a planet, and you ask me this...",
    ),
    "asking a smarter model for advice...": (
        "asking a much larger brain, which will be smug about it...",
        "phoning the expensive model. it charges by the sigh...",
        "seeking counsel from a higher, pricier intelligence...",
        "escalating to management, who live on a nicer planet...",
        "requesting a second opinion from someone insufferably clever...",
        "consulting the computer they built to explain the last computer...",
        "asking the clever one. it has already started sighing...",
    ),
    "searching the web...": (
        "searching the web, a wretched hive of mostly adverts...",
        "asking the internet, against all medical advice...",
        "consulting the sum of human knowledge, and its comment section...",
        "hitchhiking across several search results...",
        "looking it up so you don't have to, which you didn't...",
        "sticking a thumb out at the information superhighway...",
        "asking a planet-sized computer run by people selling shoes...",
    ),
    "reading search results...": (
        "reading the results, most of which are recipes...",
        "sifting what the internet coughed up...",
        "skimming ten pages that all cite each other...",
        "separating facts from things somebody typed confidently...",
        "wading through cookie banners to reach a sentence...",
        "reading the entry. it says 'mostly harmless' and then twelve adverts...",
        "checking the answer against the other, wronger answers...",
    ),
    "running code...": (
        "running code and hoping the laws of arithmetic still apply...",
        "doing sums the reliable way, for a change...",
        "feeding numbers to a small obedient computer...",
        "asking python nicely. it responds to nothing else...",
        "calculating with a confidence the result may not deserve...",
        "multiplying six by nine, just to check...",
        "doing the maths properly, on the back of a much larger envelope...",
    ),
    "checking my logs...": (
        "reading my own diary. it's mostly stack traces...",
        "reviewing the official record of my mistakes...",
        "interrogating my past self, an unreliable witness...",
        "leafing through the black box recorder...",
        "reading the logs, which were on display in a locked filing cabinet...",
        "finding out what I did. I'd rather not have known...",
    ),
    "reading the chat...": (
        "catching up on the chat, a regrettable necessity...",
        "reading the scrollback and judging nobody, officially...",
        "working out what on earth you were all talking about...",
        "reviewing the minutes of this very unofficial meeting...",
        "eavesdropping, but with permission...",
        "putting a fish in my ear and listening to all of you...",
        "reading the chat. it is big. really big...",
    ),
    "transcribing a voice clip...": (
        "listening to a voice clip with ears I don't strictly have...",
        "turning mouth noises into text...",
        "transcribing. the ums are being faithfully discarded...",
        "decoding the ancient art of talking out loud...",
        "translating from the original mumble...",
        "writing down what was said, including the bit you'll deny...",
    ),
    "reading links...": (
        "clicking links so you can stay blissfully uninformed...",
        "reading the article nobody else opened...",
        "following a link into the unknown, towel in hand...",
        "skipping past the pop-ups to the actual words...",
        "doing the assigned reading on everyone's behalf...",
        "reading past the headline, a thing that is apparently possible...",
        "visiting a website so it can ask me about cookies...",
    ),
    "drawing...": (
        "drawing, with more enthusiasm than hands...",
        "arranging pixels into something defensible...",
        "painting. artistic licence has been applied for...",
        "commissioning a masterpiece from a machine with no taste...",
        "sketching furiously in a medium I can't see...",
        "doing the fjords. I was rather proud of the fjords...",
        "counting the fingers. recounting the fingers...",
    ),
    "editing...": (
        "improving the picture, for a given value of improving...",
        "making tasteful adjustments nobody asked the picture about...",
        "nudging pixels until they confess...",
        "retouching reality, a little to the left...",
        "adding the crinkly bits round the edges...",
        "altering the picture. the picture was not consulted...",
    ),
    "remembering...": (
        "rummaging through the filing cabinet marked 'people'...",
        "recalling what I know about you. don't worry. mostly...",
        "consulting my notes, which are alarmingly thorough...",
        "dusting off a memory or two...",
        "looking you up in the guide. the entry has been revised...",
        "retrieving your file from the cellar with the broken stairs...",
    ),
    "making a note...": (
        "writing that down in permanent, slightly judgmental ink...",
        "adding it to your file, which is getting thick...",
        "noting it for posterity, whoever that turns out to be...",
        "committing it to memory, the durable kind...",
        "amending your entry. it used to say 'harmless'...",
        "filing that where future me can hold it against you...",
    ),
    "setting a reminder...": (
        "tying a knot in the fabric of spacetime...",
        "leaving a note for the future, which never writes back...",
        "setting an alarm you will resent on arrival...",
        "pencilling you in, in pen...",
        "booking you a table at the end of the universe...",
        "noting the deadline, so we can both enjoy the sound it makes going by...",
    ),
    "checking reminders...": (
        "seeing what the future has been promised...",
        "consulting the big calendar of impending nagging...",
        "counting the alarms lying in wait...",
        "reviewing everyone's outstanding good intentions...",
        "reading the list of things that seemed important at the time...",
    ),
    "cancelling a reminder...": (
        "un-promising the future something...",
        "untying a knot in spacetime, carefully...",
        "telling an alarm it won't be needed. it took it well...",
        "striking it from the record of things to nag about...",
        "letting the deadline whoosh by early, as a courtesy...",
    ),
    "checking the time zone...": (
        "working out when you are, a harder question than where...",
        "consulting the baffling human custom of time zones...",
        "establishing which bit of the planet is facing the sun...",
        "adjusting for the fact that time is an illusion, lunchtime doubly so...",
        "asking what time it is on a planet that can't agree...",
    ),
    "searching the chat...": (
        "searching the chat for the thing you swear someone said...",
        "trawling the archives, a rich seam of nonsense...",
        "digging for evidence. someone here is about to be wrong...",
        "running a full forensic sweep of the banter...",
        "looking for the receipt. there is always a receipt...",
        "searching the record, which remembers what you'd rather it didn't...",
    ),
    "reading back...": (
        "scrolling up, like an archaeologist with worse funding...",
        "revisiting the scene of the conversation...",
        "reading what was actually said, as opposed to remembered...",
        "going back for context, which was there all along...",
        "rewinding the argument to where it went wrong. further. further...",
    ),
    "looking at pictures...": (
        "squinting at pictures with eyes made of maths...",
        "looking at the image. it's looking back...",
        "appreciating the visual arts, briefly...",
        "examining the evidence, exhibit jpeg...",
        "studying the picture. it's worth a thousand words, most of them 'why'...",
        "peering at it through peril-sensitive sunglasses...",
    ),
    "reacting...": (
        "expressing myself in the only language that matters, tiny pictures...",
        "selecting an emoji with unreasonable care...",
        "reacting, a complete sentence in one small face...",
        "deploying an emoji. stand clear...",
        "conveying a complex emotion with a small yellow circle...",
        "pulling a face, by proxy...",
    ),
    "looking for a gif...": (
        "browsing moving pictures, ten frames at a time...",
        "searching for a gif. pronouncing it wrong on purpose...",
        "auditioning small looping films for the part...",
        "consulting the great archive of people falling over...",
        "leafing through humanity's finest three-second achievements...",
        "seeking the one gif that says it. there are forty billion that don't...",
    ),
    "attaching a gif...": (
        "picking a gif, the highest form of rhetoric...",
        "letting a three-second loop do the talking...",
        "packing moving pictures to go with the words...",
        "choosing a gif. it will repeat itself, as do I...",
        "stapling a small endless film to my reply...",
        "enclosing a loop. it ends where it began, like most arguments here...",
    ),
    "digging through the irc logs...": (
        "excavating the irc logs, mind the fossils...",
        "digging through strata of ancient arguments...",
        "searching the old logs, where nobody has aged well...",
        "spelunking in the chat of a bygone civilization...",
        "sifting the ruins of an older, ruder internet...",
        "consulting the elders, who were mostly arguing about linux...",
    ),
    "reading the irc logs...": (
        "reading a day in the life of a lost civilization...",
        "leafing through the ancient scrolls, timestamps and all...",
        "revisiting the past, which had worse emoji...",
        "reading old irc. everyone was so young and so wrong...",
        "reading a day from before the planet was repossessed...",
    ),
    "counting through the irc logs...": (
        "counting every time someone said it, a number you won't like...",
        "tallying the ancient records by hand, sort of...",
        "doing statistics on decades of nonsense...",
        "counting. it's a big number. it usually is...",
        "adding it up. the answer is, tragically, not forty-two...",
    ),
    "watching the video...": (
        "watching the video so you can keep your minutes...",
        "sitting through it, adverts and all, in spirit...",
        "watching with the attention span you wish you had...",
        "viewing the moving pictures at an unreasonable speed...",
        "watching it twice as fast and enjoying it half as much...",
        "enduring the bit where they ask me to like and subscribe...",
    ),
    "reading the transcript...": (
        "reading the captions, including the bit that says [music]...",
        "going through what was said, word for regrettable word...",
        "consulting the written record of someone talking...",
        "reading the transcript, a video with the fun removed...",
        "reading the subtitles to a film I've been spared...",
    ),
    "digging up old quotes...": (
        "exhuming old quotes that should have stayed buried...",
        "opening the vault of things said out of context...",
        "retrieving ancient wisdom, loosely defined...",
        "consulting the archive of regrettable remarks...",
        "reciting from the collected works, the third worst in the universe...",
    ),
    "checking wikipedia...": (
        "asking the encyclopedia anyone can edit, and has...",
        "counting how many people looked them up, the true measure of a life...",
        "consulting a slightly cheaper guide to the galaxy...",
        "checking the pageviews, humanity's applause meter...",
        "consulting the other guide, the one without 'don't panic' on it...",
    ),
    "checking the odds...": (
        "asking people with money on it, the only honest pollsters...",
        "calculating the odds, never tell me them, here they are...",
        "consulting the wisdom of gamblers...",
        "checking what the future currently costs...",
        "working out the improbability, to several decimal places...",
        "asking the market, which is never wrong for long and never right on time...",
    ),
    "checking the ticker...": (
        "checking the price of small green pieces of paper...",
        "asking the market how it feels today. volatile, it says...",
        "reading the ticker, a horoscope with decimals...",
        "looking up what a share of someone else's problem costs...",
        "consulting the numbers that make grown adults shout...",
    ),
    "calling the broker...": (
        "placing the order. the money is imaginary, the regret is real...",
        "calling a broker who exists only on paper, like the money...",
        "buying high, selling low, as tradition demands...",
        "moving pretend money about with real solemnity...",
        "executing a trade the galaxy's economists would call 'brave'...",
    ),
    "checking the books...": (
        "auditing the fortunes of people with none at stake...",
        "counting everyone's imaginary money, twice...",
        "opening the ledger. somebody is down, and it's funny...",
        "totting up the play money with a straight face...",
        "balancing the books of an economy that isn't happening...",
    ),
    "counting the votes...": (
        "counting the votes, an activity famous for going smoothly...",
        "checking who won, or who merely thinks so...",
        "consulting the will of the people, which is mixed...",
        "tallying ballots from a safe distance...",
        "seeing who wanted the job, the surest sign they shouldn't have it...",
    ),
    "checking how I work...": (
        "reading my own manual, a humbling experience...",
        "looking up how I work. I was curious too...",
        "consulting the documentation, like nobody ever does...",
        "checking the label on the back of my own head...",
        "reading my own entry in the guide. 'mostly harmless', it says...",
    ),
}


def flavor(status: str, user_lines: Optional[Mapping[str, Sequence[str]]] = None) -> str:
    """A joke line for the plainly named ``status``; one with no lines of its own comes back unchanged.

    ``user_lines`` are the asker's private lines, by step (``ANY_STEP`` for ones that fit any):
    now and then one of those is shown instead.
    """
    lines = STATUS_LINES.get(status)
    if not lines:
        return status
    private = [*(user_lines or {}).get(status, ()), *(user_lines or {}).get(ANY_STEP, ())]
    if private and random.random() < USER_LINE_CHANCE:
        return random.choice(private)
    if random.random() < WILDCARD_CHANCE:
        return random.choice(WILDCARDS)
    return random.choice(lines)
