import discord


async def get_mention_legend(channel: discord.TextChannel, bot_user: discord.User) -> str:
    # channel.members is all members who can see this channel
    lines = [f"You are <@{bot_user.id}>!"]

    for member in channel.members:
        # use nickname if set, otherwise username
        name = member.display_name
        lines.append(f"@{name} = <@{member.id}>")

    return (
        f"Here are all the users in this channel:\n"
        f"{chr(10).join(lines)}\n"
        f"Whenever you see a mention like <@USER_ID>, map it back to the corresponding handle. "
        f"If you want to @mention someone yourself use <@USER_ID> instead of @nickname for discord "
        f"to recoginize your intent."
    )
