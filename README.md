# vball-tg-ratebot
Telegram bot to rate volleyball players skill in TG group and assign a corresponding nickname tag to each player

This telegram bot helps to rate the volleyball players (telegram group users) skill level from 0.0 to 6.0 where 0 is the lowest. This version applies to classic indoor volleyball only. Players are rated by other players and the resulting skill level is displayed by changing the user's nickname tag in group chat in [2.3] format. Bot is assigned to only one telegram group. Chat topics are not supported. Ratings are anonymous for users and only bot admin has the access to the data.

### UI Language

Only english for now

### Privacy

Only the data needed for rating is collected. Personal data is not collected, except the user's telegram id. Records of games contain only datetime and list of participants. In case if database is leaked, the most sensible information is the relation between telegram users (based on games played together)


### Submit and record games

Any player can submit a game providing a date, time and list of users participated in a game. This data is collected and used only to suggest the participants to rate each other and to calculate the weight of ratings. Only games happened in the past can be submitted. User can select only players who already gave the consent to participate in rating. User should be notified about the importance and impact to rating's weights.

### Overlapping and conflicting games information

First, user provides date and time of game (game start time).
After that, user selects participants one by one. If participant is already found in another record of game with the same starting time, or selected player haven't gave the consent to be rated, show the corresponding error.

### Bot permissions

Do not store personal information like real name, age, etc
Allow bot only to write private messages to users, collect ratings and games from other players, change user's nickname tag in group.
Do not allow to mute,kick,ban users,edit messages,send messages in group chat, also disable HTML5 mini-apps.

### Skill level visibility and consent

By default, player's skill level is not displayed in name tag. No tag at all should be applied to players not participating in rating. Only players who gave the consent can be rated by other players.
If player has less than three ratings, their skill level appear with tilde symbol. example: John [~1.0]

### Rating survey

Bot asks players to rate other players.
For the first time, bot asks player to rate player itself.
After player gave self rating, bot suggests 5 other players for rating. Bot chooses players for suggestion to rate based on two parameters: number of games played together and number of rates players already have. In best scenario all the group users should be rated at least 3 times.
There should be only one rating record for each pair of players in database. If player rates other player again - the rating record should be rewrited.

### Weight of rating

Weight of rating is calculated only at the moment the rating is recorded or rewrited.
The less games played together- the less is the the weight.
The bigger is the skill level difference - the less is the weight.
Player's skill level, rater's skill level and number of games played together are stored as separate data so that the weight calculation formula and resulting weight can be adjusted later.

### Skill level estimation

Calculate and display player's skill level based on the collected ratings and their weights. Recalculate skill level and reassign new nickname tag if level is chaged after new rating is received.

### Bot UI
Description text  

Ask for consent  

Show players stats: current level, average level in each discipline, number of rates received, number of rates given, list of last 10 games recorded with datetime  

Self-rating survey  

Submit a game
* input datetime
* select participants one by one
* throw error if selected player gave no consent to participate in rating or the game with selected user and datetime already exist.

Rate another player
* select player
* show player's current level
* show number of games played together and datetime of last game
* rate skill in each discipline one by one. For each discipline, calculate average discipline rating from existing ratings, show it's criteria and ask rater if it corresponds or if it is one level up or down
* show average resulting rating
* show level change after rating

### Data tables

**Players:**

* Telegram id
* gave_consent (bool)
* is_self_rated (bool)
* Number of games played
* Number of ratings given
* Number of ratings received
* Current skill level

**Games:**
* datetime
* players (m2m)

**Ratings:**
datetime
player (o2m)
player_level
rater (o2m)
rater_level
rating_serve
rating_serve_receive
rating_setting
rating_attack
rating_blocking
rating_defence
avg_rating
games_count
weight


