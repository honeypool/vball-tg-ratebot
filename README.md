# vball-tg-ratebot
Telegram bot to rate volleyball players skill in TG group and assign a corresponding nickname tag to each player

This telegram bot helps to rate the volleyball players (telegram group users) skill level from 0.0 to 6.0 where 0 is the lowest. This version applies to classic indoor volleyball only. Players are rated by other players and the resulting skill level is displayed by changing the user's nickname tag in group chat in [2.3] format. Bot is assigned to only one telegram group. Chat topics are not supported. Ratings are anonymous for users and only bot admin has the access to the data.

### UI Language

Only english for now

### Privacy

Only the data needed for rating is collected. Personal data is not collected, except the user's telegram id. Records of games contain only datetime and list of participants. In case if database is leaked, the most sensible information is the relation between telegram users (based on games played together)


### Submit and record games

Any player can submit a game providing a date, time and list of users participated in a game. This data is collected and used only to suggest the participants to rate each other and to calculate the weight of ratings. Only games happened in the past can be submitted. User can select players even if they are not members of a telegram group. In that case user is suggested to personally invite the player to the telegram group and to give consent for rating. User can select only players who already gave the consent to participate in rating. User should be notified about the importance and impact to rating's weights.

### Overlapping and conflicting games information

First, user provides date and time of game (game start time).
After that, user selects participants one by one. If participant is already found in another record of game with the same starting time, or selected player haven't gave the consent to be rated, show the corresponding error.

### Bot permissions

Do not store personal information like real name, age, etc
Allow bot only to write private messages to users, collect ratings and games from other players, change user's nickname tag in group.
Do not allow to mute,kick,ban users,edit messages,send messages in group chat, also disable HTML5 mini-apps.

### Skill level visibility and consent

By default, player's skill level is not displayed in name tag. No tag at all should be applied to players not participating in rating. Player should give a consent before making their skill level or ratings visible by other players. Only players who gave the consent can be rated by other players.
If player has less than three ratings, their skill level appear with tilde symbol. example: John [~1.0]

### Rating survey

Bot asks players to rate other players.
For the first time, bot asks player to rate player itself.
After player gave self rating, bot suggests 5 other players for rating. Bot chooses players for suggestion to rate based on two parameters: number of games played together and number of players already rated the player. In best scenario all the group users should be rated at least 3 times.
There should be only one rating record for each pair of players in database. If player rates other player again - the rating record should be rewrited.

### Rating survey

After survey is finished, the rating record is created in the database.

### Weight of rating

Weight of rating is calculated only at the moment the rating is recorded or rewrited.
The less games played together- the less is the the weight.
The bigger is the skill level difference - the less is the weight.
Player's skill level, rater's skill level and number of games played together are stored as separate data so that the weight calculation formula and resulting weight can be adjusted later.

### Skill level estimation

Calculate and display player's skill level based on the collected ratings and their weights. Recalculate skill level and reassign new nickname tag if level is chaged after new rating is received.

### Data tables

**Players:**

* Telegram id
* Number of games played
* Number of ratings given
* Number of ratings received
* Current skill level

**Games:**

**Ratings:**

