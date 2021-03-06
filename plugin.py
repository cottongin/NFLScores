###
# Limnoria plugin to retrieve results from NFL.com using their (undocumented)
# JSON API.
# Copyright (c) 2016, Santiago Gil
# adapted by cottongin
#
#     This program is free software: you can redistribute it and/or modify
#     it under the terms of the GNU General Public License as published by
#     the Free Software Foundation, either version 3 of the License, or
#     (at your option) any later version.
#
#     This program is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU General Public License for more details.
#
#     You should have received a copy of the GNU General Public License
#     along with this program.  If not, see <http://www.gnu.org/licenses/>.
###

import supybot.utils as utils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks
try:
    from supybot.i18n import PluginInternationalization
    _ = PluginInternationalization('NFLscores')
except ImportError:
    # Placeholder that allows to run the plugin on a bot
    # without the i18n module
    _ = lambda x: x

import datetime
import dateutil.parser
import json
import pytz
import urllib.request
import lxml.etree as lxml
from collections import OrderedDict


class NFLScores(callbacks.Plugin):
    """Get scores from NFL.com."""
    def __init__(self, irc):
        self.__parent = super(NFLScores, self)
        self.__parent.__init__(irc)

        self._SCOREBOARD_ENDPOINT = ('http://www.nfl.com/liveupdate'
                                     '/scorestrip/ss.xml')
        #self._SCOREBOARD_ENDPOINT = ('http://www.nfl.com/liveupdate'
        #                             '/scorestrip/postseason/ss.xml')
        self._GAME_URL = ('http://www.nfl.com/liveupdate'
                          '/game-center/{}/{}_gtd.json')

        #self._FUZZY_DAYS = ['yesterday', 'tonight', 'today', 'tomorrow']

        # These two variables store the latest data acquired from the server
        # and its modification time. It's a one-element cache.
        # They are used to employ HTTP's 'If-Modified-Since' header and
        # avoid unnecessary downloads for today's information (which will be
        # requested all the time to update the scores).
        self._today_scores_cached_url = None
        self._today_scores_last_modified_time = None
        self._today_scores_last_modified_data = None
        self._today_json_cached_url = None
        self._today_json_last_modified_time = None
        self._today_json_last_modified_data = None

    def nfl(self, irc, msg, args, optional_team): # optional_team, optional_date):
        """
        Get games for the current week, optionally filter by team.
        """

        if optional_team is None:
            team = "ALL"
            irc.reply(self._getTodayGames(team))
        elif optional_team == '*':
            nf = self._getTodayGames('NOTFINAL')
            f = self._getTodayGames('FINAL')
            print(len(nf),len(f))
            if nf != 'No games found':
                irc.reply(nf)
            if f != 'No games found':
                irc.reply(f)
        else:
            team = optional_team.upper()
            irc.reply(self._getTodayGames(team))

    nfl = wrap(nfl, [optional('somethingWithoutSpaces')])

    def nflgamestats(self, irc, msg, args, team): # optional_team, optional_date):
        """<team>
        Get current game stats for the given team.
        """

        team = team.upper()
        irc.reply(self._getTodayGamesStats(team))

    nflgamestats = wrap(nflgamestats, [('somethingWithoutSpaces')])

    def _getTodayGames(self, team):
        games = self._getGames(team, self._getTodayDate())
        return self._resultAsString(games, team)

    def _getTodayGamesStats(self, team):
        games = self._getGameStats(team, self._getTodayDate())
        return self._statsAsString(games, team)

    def _getGamesForDate(self, team, date):
        games = self._getGames(team, date)
        return self._resultAsString(games)

############################
# Content-getting helpers
############################
    def _getGames(self, team, date):
        """Given a date, populate the url with it and try to download its
        content. If successful, parse the JSON data and extract the relevant
        fields for each game. Returns a list of games."""
        sched_url = self._SCOREBOARD_ENDPOINT
        base_games_url = self._GAME_URL

        # (If asking for today's results, enable the 'If-Mod.-Since' flag)
        use_cache = (date == self._getTodayDate())
        response = self._getURL(sched_url, use_cache)
        games = self._getGamesSch(response, team)
        print(games)
        games = self._getGamesJson(base_games_url, games, use_cache)
        games = self._parseGames(games, team)

        return games

    def _getGameStats(self, team, date):
        """Given a date, populate the url with it and try to download its
        content. If successful, parse the JSON data and extract the relevant
        fields for each game. Returns a list of games."""
        sched_url = self._SCOREBOARD_ENDPOINT
        base_games_url = self._GAME_URL

        # (If asking for today's results, enable the 'If-Mod.-Since' flag)
        use_cache = (date == self._getTodayDate())
        response = self._getURL(sched_url, use_cache)
        games = self._getGamesSch(response, team)
        #print(games)
        games = self._getGamesJson(base_games_url, games, use_cache)
        games = self._parseStats(games, team)

        return games

    def _getGamesJson(self, url, data, use_cache):
        """This is a kludgy mess to find out if there is json data associated
        with a given game."""

        for game in data:
            url2 = url.format(game['eid'], game['eid'])
            try:
                response = self._getURL(url2, use_cache)
                json = self._extractJSON(response)
                game['json'] = json[game['eid']]
            except:
                game['json'] = None

        return data

    def _getGamesSch(self, data, team):
        xml = lxml.fromstring(data)
        games = []
        for g in xml.xpath("//g"):
            # For a specific team, only parse out that team
            if team in g.get('h') or team in g.get('v'):
                gsis_id = g.get('eid')
                games.append({
                    'eid': gsis_id,
                    'wday': g.get('d'),
                    'year': xml.find("gms").get('y'),
                    'month': int(gsis_id[4:6]),
                    'day': int(gsis_id[6:8]),
                    'time': g.get('t'),
                    'meridiem': None,
                    'season_type': g.get('gt'),
                    'week': None,
                    'home': g.get('h'),
                    'away': g.get('v'),
                    'gamekey': g.get('gsis'),
                })
            # For every team, or and for games in progress
            elif team == 'ALL' or team == '--IP':
                gsis_id = g.get('eid')
                games.append({
                    'eid': gsis_id,
                    'wday': g.get('d'),
                    'year': xml.find("gms").get('y'),
                    'month': int(gsis_id[4:6]),
                    'day': int(gsis_id[6:8]),
                    'time': g.get('t'),
                    'meridiem': None,
                    'season_type': g.get('gt'),
                    'week': xml.find("gms").get('w'),
                    'home': g.get('h'),
                    'away': g.get('v'),
                    'gamekey': g.get('gsis'),
                })
            # For games just today
            elif team == 'TODAY':
                gsis_id = g.get('eid')
                tdate = datetime.datetime.now().day
                if tdate == int(gsis_id[6:8]):
                    games.append({
                        'eid': gsis_id,
                        'wday': g.get('d'),
                        'year': xml.find("gms").get('y'),
                        'month': int(gsis_id[4:6]),
                        'day': int(gsis_id[6:8]),
                        'time': g.get('t'),
                        'meridiem': None,
                        'season_type': g.get('gt'),
                        'week': None,
                        'home': g.get('h'),
                        'away': g.get('v'),
                        'gamekey': g.get('gsis'),
                    })
            # For games just tomorrow
            elif team == 'TOMORROW':
                gsis_id = g.get('eid')
                tdate = datetime.datetime.now().day + 1
                if tdate == int(gsis_id[6:8]):
                    games.append({
                        'eid': gsis_id,
                        'wday': g.get('d'),
                        'year': xml.find("gms").get('y'),
                        'month': int(gsis_id[4:6]),
                        'day': int(gsis_id[6:8]),
                        'time': g.get('t'),
                        'meridiem': None,
                        'season_type': g.get('gt'),
                        'week': None,
                        'home': g.get('h'),
                        'away': g.get('v'),
                        'gamekey': g.get('gsis'),
                    })
            # For games just yesterday
            elif team == 'YESTERDAY':
                gsis_id = g.get('eid')
                tdate = datetime.datetime.now().day - 1
                if tdate == int(gsis_id[6:8]):
                    games.append({
                        'eid': gsis_id,
                        'wday': g.get('d'),
                        'year': xml.find("gms").get('y'),
                        'month': int(gsis_id[4:6]),
                        'day': int(gsis_id[6:8]),
                        'time': g.get('t'),
                        'meridiem': None,
                        'season_type': g.get('gt'),
                        'week': None,
                        'home': g.get('h'),
                        'away': g.get('v'),
                        'gamekey': g.get('gsis'),
                    })

            # This runs for the '*' argument
            elif 'FINAL' in team:
                gsis_id = g.get('eid')
                games.append({
                    'eid': gsis_id,
                    'wday': g.get('d'),
                    'year': xml.find("gms").get('y'),
                    'month': int(gsis_id[4:6]),
                    'day': int(gsis_id[6:8]),
                    'time': g.get('t'),
                    'meridiem': None,
                    'season_type': g.get('gt'),
                    'week': xml.find("gms").get('w'),
                    'home': g.get('h'),
                    'away': g.get('v'),
                    'gamekey': g.get('gsis'),
                })

        for game in games:
            h = int(game['time'].split(':')[0])
            print(h)
            m = int(game['time'].split(':')[1])
            if 0 < h <= 12:  # All games before "9:00" are PM until proven otherwise
                game['meridiem'] = 'PM'

            if game['meridiem'] is None:

                days_games = [g for g in games if g['wday'] == game['wday']]
                preceeding = [g for g in days_games if g['eid'] < game['eid']]
                proceeding = [g for g in days_games if g['eid'] > game['eid']]

                #print(days_games, preceeding, proceeding)
                # for g in proceeding:
                #     print(g)

                # If any games *after* this one are AM then so is this
                if any(g['meridiem'] == 'AM' for g in proceeding):
                    game['meridiem'] = 'AM'
                # If any games *before* this one are PM then so is this one
                elif any(g['meridiem'] == 'PM' for g in preceeding):
                    game['meridiem'] = 'PM'
                # If any games *after* this one have an "earlier" start it's AM
                elif any(h > t for t in [int(g['time'].split(':')[0]) for g in proceeding]):
                    game['meridiem'] = 'AM'
                # If any games *before* this one have a "later" start time it's PM
                elif any(h < t for t in [int(g['time'].split(':')[0]) for g in preceeding]):
                    game['meridiem'] = 'PM'

            if game['meridiem'] is None:
                if game['wday'] not in ['Sat', 'Sun']:
                    game['meridiem'] = 'PM'
                if game['season_type'] == 'POST':
                    game['meridiem'] = 'PM'

        return games

    def _getURL(self, url, use_cache=False):
        """Use urllib to download the URL's content. The use_cache flag enables
        the use of the one-element cache, which will be reserved for today's
        games URL. (In the future we could implement a real cache with TTLs)."""
        user_agent = 'Mozilla/5.0 \
                      (X11; Ubuntu; Linux x86_64; rv:45.0) \
                      Gecko/20100101 Firefox/45.0'
        header = {'User-Agent': user_agent}

        # ('If-Modified-Since' to avoid unnecessary downloads.)
        if use_cache and self._haveCachedData(url):
            header['If-Modified-Since'] = self._today_scores_last_modified_time

        request = urllib.request.Request(url, headers=header)

        try:
            response = urllib.request.urlopen(request, timeout=2)
        except urllib.error.HTTPError as error:
            if use_cache and error.code == 304: # Cache hit
                self.log.info("{} - 304"
                              "(Last-Modified: "
                              "{})".format(url, self._cachedDataLastModified()))
                return self._cachedData()
            else:
                self.log.error("HTTP Error ({}): {}".format(url, error.code))
                pass

        self.log.info("{} - 200".format(url))

        if not use_cache:
            return response.read()

        # Updating the cached data:
        self._updateCache(url, response)
        return self._cachedData()

    def _extractJSON(self, body):
        return json.loads(body.decode('utf-8'))

    def _parseGames(self, data, team):
        """Extract all relevant fields from NFL.com's scoreboard.json
        and return a list of games."""
        games = []
        for g in data:

            # Starting times are in UTC. By default, we will show Eastern times.
            # (In the future we could add a user option to select timezones.)
            # starting_time = '{} {}{}'.format(g['wday'], g['time'], g['meridiem'])
            starting_time = '{} {}'.format(g['wday'], g['time'])
            if not g['json']:
                game_info = {'home_team': g['home'],
                             'away_team': g['away'],
                             'starting_time': starting_time,
                             'starting_time_TBD': False,
                             'clock': None,
                             'period': 0,
                             'ended': False,
                             'week': ('Week ' + g['week'] + ': ' if g['week'] else ''),
                             'date': g['day'],
                            }
            else:
                # First see if there's a last play in the json
                try:
                    mp = 0
                    for p,v in g['json']['drives'][str(g['json']['drives']['crntdrv'])]['plays'].items():
                        if int(p) > mp:
                            mp = int(p)
                    try:
                        lp = g['json']['drives'][str(g['json']['drives']['crntdrv'])]['plays'][str(mp)]['desc']
                    except:
                        lp = ''
                except:
                    lp = ''
                game_info = {'home_team': g['home'],
                             'away_team': g['away'],
                             'home_score': g['json']['home']['score']['T'],
                             'away_score': g['json']['away']['score']['T'],
                             'starting_time': starting_time,
                             'starting_time_TBD': False,
                             'clock': g['json']['clock'],
                             'period': g['json']['qtr'],
                             'redzone': g['json']['redzone'],
                             'posteam': g['json']['posteam'],
                             'yardline': g['json']['yl'],
                             'down': ('1' if g['json']['down'] is None else g['json']['down']),
                             'togo': ('' if g['json']['togo'] == 0 else g['json']['togo']),
                             'lastplay': lp,
                             'ended': (g['json']['qtr'] == 'Final' or g['json']['qtr'] == 'final overtime'),
                             'week': ('Week ' + g['week'] + ': ' if g['week'] else ''),
                             'date': g['day'],
                            }
            if team == "--IP":
                if game_info['clock'] and not game_info['ended'] and game_info['period'] != 'Pregame':
                    games.append(game_info)
            elif team == "NOTFINAL":
                if not game_info['ended']:
                    games.append(game_info)
            elif team == 'FINAL':
                if game_info['ended']:
                    games.append(game_info)
            else:
                games.append(game_info)

        return games

    def _parseStats(self, data, team):
        """Extract all relevant fields from NFL.com's scoreboard.json
        and return a list of games."""
        games = []
        for g in data:
            #print(g['home'], g['away'], team)
            # Starting times are in UTC. By default, we will show Eastern times.
            # (In the future we could add a user option to select timezones.)
            # starting_time = '{} {}{}'.format(g['wday'], g['time'], g['meridiem'])
            starting_time = '{} {}'.format(g['wday'], g['time'])
            if not g['json']:
                game_info = {'home_team': g['home'],
                             'away_team': g['away'],
                             'starting_time': starting_time,
                             'starting_time_TBD': False,
                             'clock': None,
                             'period': 0,
                             'ended': False,
                             'week': ('Week ' + g['week'] + ': ' if g['week'] else ''),
                             'date': g['day'],
                            }
            else:
                if team in g['home'] and len(team) == len(g['home']):
                    game_info = {'home_team': g['home'],
                                'away_team': g['away'],
                                'home_score': g['json']['home']['score']['T'],
                                'away_score': g['json']['away']['score']['T'],
                                'starting_time': starting_time,
                                'starting_time_TBD': False,
                                'clock': g['json']['clock'],
                                'redzone': g['json']['redzone'],
                                'posteam': g['json']['posteam'],
                                'period': g['json']['qtr'],
                                'ended': (g['json']['qtr'] == 'Final' or g['json']['qtr'] == 'final overtime'),
                                'week': ('Week ' + g['week'] + ': ' if g['week'] else ''),
                                'date': g['day'],
                                'firstdowns': g['json']['home']['stats']['team']['totfd'],
                                'yards': g['json']['home']['stats']['team']['totyds'],
                                'pyards': g['json']['home']['stats']['team']['pyds'],
                                'ryards': g['json']['home']['stats']['team']['ryds'],
                                'flags': g['json']['home']['stats']['team']['pen'],
                                'flagyds': g['json']['home']['stats']['team']['penyds'],
                                'trnovrs': g['json']['home']['stats']['team']['trnovr'],
                                'punts': g['json']['home']['stats']['team']['pt'],
                                'puntyds': g['json']['home']['stats']['team']['ptyds'],
                                'puntavg': g['json']['home']['stats']['team']['ptavg'],
                                'top': g['json']['home']['stats']['team']['top'],
                                }
                elif team in g['away'] and len(team) == len(g['away']):
                    game_info = {'home_team': g['home'],
                                'away_team': g['away'],
                                'home_score': g['json']['home']['score']['T'],
                                'away_score': g['json']['away']['score']['T'],
                                'starting_time': starting_time,
                                'starting_time_TBD': False,
                                'clock': g['json']['clock'],
                                'redzone': g['json']['redzone'],
                                'posteam': g['json']['posteam'],
                                'period': g['json']['qtr'],
                                'ended': (g['json']['qtr'] == 'Final' or g['json']['qtr'] == 'final overtime'),
                                'week': ('Week ' + g['week'] + ': ' if g['week'] else ''),
                                'date': g['day'],
                                'firstdowns': g['json']['away']['stats']['team']['totfd'],
                                'yards': g['json']['away']['stats']['team']['totyds'],
                                'pyards': g['json']['away']['stats']['team']['pyds'],
                                'ryards': g['json']['away']['stats']['team']['ryds'],
                                'flags': g['json']['away']['stats']['team']['pen'],
                                'flagyds': g['json']['away']['stats']['team']['penyds'],
                                'trnovrs': g['json']['away']['stats']['team']['trnovr'],
                                'punts': g['json']['away']['stats']['team']['pt'],
                                'puntyds': g['json']['away']['stats']['team']['ptyds'],
                                'puntavg': g['json']['away']['stats']['team']['ptavg'],
                                'top': g['json']['away']['stats']['team']['top'],
                                }
                else:
                    pass
            if team == "--IP":
                if game_info['clock'] and not game_info['ended'] and game_info['period'] != 'Pregame':
                    games.append(game_info)
            elif team == "NOTFINAL":
                if not game_info['ended']:
                    games.append(game_info)
            elif team == 'FINAL':
                if game_info['ended']:
                    games.append(game_info)
            else:
                games.append(game_info)

        return games

############################
# Today's games cache
############################
    def _cachedData(self):
        return self._today_scores_last_modified_data

    def _haveCachedData(self, url):
        return (self._today_scores_cached_url == url) and \
                (self._today_scores_last_modified_time is not None)

    def _cachedDataLastModified(self):
        return self._today_scores_last_modified_time

    def _updateCache(self, url, response):
        self._today_scores_cached_url = url
        self._today_scores_last_modified_time = response.headers['last-modified']
        self._today_scores_last_modified_data = response.read()

############################
# Formatting helpers
############################
    def _statsAsString(self, games, team=None):
        if len(games) == 0:
            return "No games found"
        else:
            s = sorted(games, key=lambda k: k['ended']) #, reverse=True)
            b = []
            for g in s:
                b.append(self._statToString(g, team))
            return "{} {}".format(ircutils.bold(ircutils.mircColor(team + ' Game Stats:', 'red')), ' | '.join(b))

    def _statToString(self, game, team=None):
        """ Given a game, format the information into a string according to the
        context. For example:
        "MEM @ CLE 07:00 PM ET" (a game that has not started yet),
        "HOU 132 GSW 127 F OT2" (a game that ended and went to 2 overtimes),
        "POR 36 LAC 42 8:01 Q2" (a game in progress)."""
        away_team = game['away_team']
        home_team = game['home_team']
        if game['period'] == 'Final':
            game['period'] = 4
            game['ended'] = True
        elif game ['period'] == 'Pregame':
            game['period'] = 0
        elif game['period'] == 'Halftime':
            game['period'] = 9
        elif game['period'] == 'final overtime':
            game['period'] = 5
            game['ended'] = True
        else:
            game['period'] = int(game['period'])
        if game['period'] == 0: # The game hasn't started yet
            starting_time = game['starting_time'] \
                            if not game['starting_time_TBD'] \
                            else "TBD"
            return "{} @ {} {}".format(away_team, home_team, starting_time)

        # The game started => It has points:
        away_score = game['away_score']
        home_score = game['home_score']

        away_string = "{} {}".format(away_team, away_score)
        home_string = "{} {}".format(home_team, home_score)

        # Highlighting 'red zone' teams:
        if game['redzone'] and not game['ended'] and game['period'] != 9:
            if away_team in game['posteam']:
                away_string = ircutils.mircColor(away_string, 'red')
            if home_team in game['posteam']:
                home_string = ircutils.mircColor(home_string, 'red')

        # Bold for the winning team:
        if int(away_score) > int(home_score):
            away_string = ircutils.bold(away_string)
        elif int(home_score) > int(away_score):
            home_string = ircutils.bold(home_string)

        game_string = "{} {} {}".format(away_string, home_string,
                                        self._clockBoardToString(game['clock'],
                                                                game['period'],
                                                                game['ended']))
        # Add stats
        if team != "ALL" and team != '--IP' and game['period'] != 9: # and not game['ended'] and 'FINAL' not in team:
            if len(team) <= 3:                 #  fd      ty       py      ry      flags           tos      punts          top
                game_string = game_string + " :: {} {} | {} {} | {} {} | {} {} | {} {} ({} yds) | {} {} | {} {} ({} avg) | {} {}".format(ircutils.bold('First Downs:'), game['firstdowns'],
                                                                                                                                           ircutils.bold('Total Yards:'), game['yards'],
                                                                                                                                           ircutils.bold('Passing Yards:'), game['pyards'],
                                                                                                                                           ircutils.bold('Rushing Yards:'), game['ryards'],
                                                                                                                                           ircutils.bold('Flags:'), game['flags'], game['flagyds'],
                                                                                                                                           ircutils.bold('Turnovers:'), game['trnovrs'],
                                                                                                                                           ircutils.bold('Punts:'), game['punts'], game['puntavg'],
                                                                                                                                           ircutils.bold('Time of Poss.:'), game['top'])
        return game_string

    def _resultAsString(self, games, team=None):
        if len(games) == 0:
            return "No games found"
        else:
            s = sorted(games, key=lambda k: k['ended']) #, reverse=True)
            b = []
            for g in s:
                b.append(self._gameToString(g, team))
            return "{}{}".format(ircutils.bold(games[0]['week']), ' | '.join(b))

    def _gameToString(self, game, team=None):
        """ Given a game, format the information into a string according to the
        context. For example:
        "MEM @ CLE 07:00 PM ET" (a game that has not started yet),
        "HOU 132 GSW 127 F OT2" (a game that ended and went to 2 overtimes),
        "POR 36 LAC 42 8:01 Q2" (a game in progress)."""
        away_team = game['away_team']
        home_team = game['home_team']
        if game['period'] == 'Final':
            game['period'] = 4
            game['ended'] = True
        elif game ['period'] == 'Pregame':
            game['period'] = 0
        elif game['period'] == 'Halftime':
            game['period'] = 9
        elif game['period'] == 'final overtime':
            game['period'] = 5
            game['ended'] = True
        else:
            game['period'] = int(game['period'])
        if game['period'] == 0: # The game hasn't started yet
            starting_time = game['starting_time'] \
                            if not game['starting_time_TBD'] \
                            else "TBD"
            return "{} @ {} {}".format(away_team, home_team, starting_time)

        # The game started => It has points:
        away_score = game['away_score']
        home_score = game['home_score']

        away_string = "{} {}".format(away_team, away_score)
        home_string = "{} {}".format(home_team, home_score)

        # Highlighting 'red zone' teams:
        if game['redzone'] and not game['ended'] and game['period'] != 9:
            if away_team in game['posteam']:
                away_string = ircutils.mircColor(away_string, 'red')
            if home_team in game['posteam']:
                home_string = ircutils.mircColor(home_string, 'red')

        # Bold for the winning team:
        if int(away_score) > int(home_score):
            away_string = ircutils.bold(away_string)
        elif int(home_score) > int(away_score):
            home_string = ircutils.bold(home_string)

        game_string = "{} {} {}".format(away_string, home_string,
                                        self._clockBoardToString(game['clock'],
                                                                game['period'],
                                                                game['ended']))
        # Add last play summary
        if team != "ALL" and team != '--IP' and game['period'] != 9 and not game['ended'] and 'FINAL' not in team:
            if len(team) <= 3:
                game_string = game_string + " :: {} has possession at {} ({} and {}) :: Last play: {}".format(game['posteam'],
                                                                                        game['yardline'],
                                                                                        game['down'],
                                                                                        game['togo'],
                                                                                        game['lastplay'],
                                                                                       )
        return game_string

    def _clockBoardToString(self, clock, period, game_ended):
        """Get a string with current period and, if the game is still
        in progress, the remaining time in it."""
        period_number = period
        # Game hasn't started => There is no clock yet.
        if period_number == 0:
            return ""

        # Halftime
        if period == 9:
            return ircutils.mircColor('Halftime', 'orange')

        period_string = self._periodToString(period_number)

        # Game finished:
        if game_ended:
            if period_number == 4:
                return ircutils.mircColor('F', 'red')
            else:
                return ircutils.mircColor("F {}".format(period_string), 'red')

        # Game in progress:
        else:
            # Period in progress, show clock:
            return "{} {}".format(clock, ircutils.mircColor(period_string, 'green'))

    def _periodToString(self, period):
        """Get a string describing the current period in the game.
        period is an integer counting periods from 1 (so 5 would be OT1).
        The output format is as follows: {Q1...Q4} (regulation);
        {OT, OT2, OT3...} (overtimes)."""
        if period <= 4:
            return "Q{}".format(period)

        ot_number = period - 4
        if ot_number == 1:
            return "OT"
        return "OT{}".format(ot_number)

############################
# Date-manipulation helpers
############################
    def _getTodayDate(self):
        """Get the current date formatted as "YYYYMMDD".
        Because the API separates games by day of start, we will consider and
        return the date in the Pacific timezone.
        The objective is to avoid reading future games anticipatedly when the
        day rolls over at midnight, which would cause us to ignore games
        in progress that may have started on the previous day.
        Taking the west coast time guarantees that the day will advance only
        when the whole continental US is already on that day."""
        today = self._pacificTimeNow().date()
        today_iso = today.isoformat()
        return today_iso.replace('-', '')

    def _easternTimeNow(self):
        return datetime.datetime.now(pytz.timezone('US/Eastern'))

    def _pacificTimeNow(self):
        return datetime.datetime.now(pytz.timezone('US/Pacific'))

    def _ISODateToEasternTime(self, iso):
        """Convert the ISO date in UTC time that the API outputs into an
        Eastern time formatted with am/pm. (The default human-readable format
        for the listing of games)."""
        date = dateutil.parser.parse(iso)
        date_eastern = date.astimezone(pytz.timezone('US/Eastern'))
        eastern_time = date_eastern.strftime('%-I:%M %p')
        return "{} ET".format(eastern_time) # Strip the seconds

    def _stripDateSeparators(self, date_string):
        return date_string.replace('-', '')

    def _EnglishDateToDate(self, date):
        """Convert a human-readable like 'yesterday' to a datetime object
        and return a 'YYYYMMDD' string."""
        if date == "lastweek":
            day_delta = -7
        elif date == "yesterday":
            day_delta = -1
        elif date == "today" or date =="tonight":
            day_delta = 0
        elif date == "tomorrow":
            day_delta = 1
        elif date == "nextweek":
            day_delta = 7
        # Calculate the day difference and return a string
        date_string = (self._pacificTimeNow() +
                      datetime.timedelta(days=day_delta)).strftime('%Y%m%d')
        return date_string

    def _checkDateInput(self, date):
        """Verify that the given string is a valid date formatted as
        YYYY-MM-DD. Also, the API seems to go back until 2014-10-04, so we
        will check that the input is not a date earlier than that."""

        #weeks = {'pre1':['2016-08-11', '2016-08-14'],
        #         'pre2':['2016-08-', '2016-08-'],
        #         'pre3':['2016-08-', '2016-08-'],
        #         'pre4':['2016-']}

        if date is None:
            return None

        if date in self._FUZZY_DAYS:
            date = self._EnglishDateToDate(date)
        elif date.replace('-','').isdigit():
            try:
                parsed_date = datetime.datetime.strptime(date, '%Y-%m-%d')
            except:
                raise ValueError('Incorrect date format, should be YYYY-MM-DD')

            # The current API goes back until 2014-10-04. Is it in range?
            if parsed_date.date() <  datetime.date(2014, 10, 4):
                raise ValueError('I can only go back until 2014-10-04')
        else:
            return None

        return self._stripDateSeparators(date)

Class = NFLScores

# vim:set shiftwidth=4 softtabstop=4 expa
