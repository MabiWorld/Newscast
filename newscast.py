#!/usr/bin/env python3
#-*- coding:utf-8 -*-

import re
import csv
import logging
import mwclient
import urllib.request
from math import ceil
from bs4 import BeautifulSoup, element as bs4_element

import dateutil.parser
from datetime import datetime, timedelta

import config

logging.basicConfig(level=logging.WARNING)

def add_year(date, posted=datetime.now()):
	if isinstance(posted, str):
		posted = dateutil.parser.parse(posted)
		posted = datetime(posted.year, posted.month, posted.day)
	#endif

	date = dateutil.parser.parse(date)
	if date < posted:
		return datetime(date.year + 1, date.month, date.day,
			date.hour, date.minute, date.second, date.microsecond)
	else:
		return date
	#endif
#enddef

def previous_sibling(elem):
	p = elem.previous_sibling
	while isinstance(p, bs4_element.NavigableString):
		p = p.previous_sibling
	#endwhile
	return p
#enddef

def next_sibling(elem):
	p = elem.next_sibling
	while isinstance(p, bs4_element.NavigableString):
		p = p.next_sibling
	#endwhile
	return p
#enddef

ORDINAL = ("th", "st", "nd", "rd", "th", "th", "th", "th", "th", "th")
def ordinal(num):
	if 10 <= num <= 20:
		return "th"
	else:
		return ORDINAL[num % 10]
	#endif
#enddef

class NexonNews:
	URL_ALL = "http://mabinogi.nexon.net/News/All"
	URL_ALL_ARTICLE ="http://mabinogi.nexon.net/News/All/1/%s"
	URL_WIKI_BASE = "wiki.mabinogiworld.com"
	URL_WIKI_PATH = "/"
	URL_WIKI_NEWS = "Wiki_Home/WikiUpdates"

	GET_ID = re.compile(r'/News/All/1/([^/]+)')
	GET_TZ = re.compile(r'.*?\(([^,]+)')
	SHOP_LINK = re.compile(r'/Shop/WebShop/Detail/Cash/(\d+)')
	SHOP_TITLE = re.compile(r'([A-Z][a-zA-Z]*\b(\s+|$))+')
	MONTH_DAY = re.compile(r'([a-zA-Z]+) (\d+)')
	WIKI_ENTRY = re.compile(r"''([^']+)''(.*?)(?=^''|\Z)", re.M | re.S)
	SUP = re.compile(r'<sup>.*?</sup>', re.I)

	KNOWN_FILE = "known.csv"

	TYPE_ORDER = ["maint", "event", "*", "sale", "unknown"]
	# index = post index
	# name = post title
	# posted = date of nexon's post
	# start = starting datetime of sale/event/maint
	# end = ending datetime of sale/event/maint
	MESSAGES = {
		"maint": "{{{{:Wiki Home/Maintenance|isScheduled={}|isUpdate={}|date={start:%B} {start.day}<sup>{start_o}</sup>|startTimePacific={start:%I:%M %p}|endTimePacific={end:%I:%M %p}|DST={}|length={}|src={index}|ended={}}}}}",
		"event": "*The [[{name}]] has started. For more information, see [http://mabinogi.nexon.net/News/Announcements/1/{index} here.]",
		"sale": {
			"00": "*The [[{}]] is now available for a limited time from ??? to ???. For more information, see [http://mabinogi.nexon.net/News/Announcements/1/{index} here.]",
			"01": "*The [[{}]] is now available for a limited time from ??? to {end:%B} {end.day}<sup>{end_o}</sup>. For more information, see [http://mabinogi.nexon.net/News/Announcements/1/{index} here.]",
			"10": "*The [[{}]] is now available for a limited time from {start:%B} {start.day}<sup>{start_o}</sup> to ???. For more information, see [http://mabinogi.nexon.net/News/Announcements/1/{index} here.]",
			"11": "*The [[{}]] is now available for a limited time from {start:%B} {start.day}<sup>{start_o}</sup> to {end:%B} {end.day}<sup>{end_o}</sup>. For more information, see [http://mabinogi.nexon.net/News/Announcements/1/{index} here.]",
		},
		"unknown": "*[http://mabinogi.nexon.net/News/All/1/{index} {name}] (Please add details.)",
	}

	def __init__(self):
		# idx: name, tag, type, post date, start date, end date, when to post, *other info
		# when to post
		#  0 - don't post
		#  1 - immediately (post date)
		#  2 - delayed (start date)
		#  x - posted already
		self.known = {}
		self.reload_known()

		self.wiki = None
	#enddef

	## Persistent memory ##
	def reload_known(self):
		known = {}
		try:
			with open(self.KNOWN_FILE, encoding="utf8") as f:
				reader = csv.reader(f)

				for line in reader:
					if not line: continue
					idx, name, tag, post_type, post_date, start_date, end_date, when_post, *args = line
					if post_date: post_date = dateutil.parser.parse(post_date)
					if start_date: start_date = dateutil.parser.parse(start_date)
					if end_date: end_date = dateutil.parser.parse(end_date)
					known[idx] = (name, tag, post_type, post_date, start_date, end_date, when_post, *args)
				#endfor
			#endwith
		except FileNotFoundError:
			pass
		#endtry
		self.known = known
	#enddef

	def save_known(self):
		with open(self.KNOWN_FILE, "w", encoding="utf8") as f:
			writer = csv.writer(f)

			for idx, (name, tag, post_type, post_date, start_date, end_date, when_post, *args) in self.known.items():
				if post_date: post_date = post_date.isoformat()
				if start_date: start_date = start_date.isoformat()
				if end_date: end_date = end_date.isoformat()
				writer.writerow([idx, name, tag, post_type, post_date, start_date, end_date, when_post, *args])
			#endfor
		#endwith
	#enddef

	## Download news ##
	def fetch_news_list(self):
		with urllib.request.urlopen(self.URL_ALL) as response:
			html = response.read()

			page = BeautifulSoup(html, "lxml")
			tbody = page.find(id="m-page").find(class_="list").tbody

			articles = []
			for tr in tbody.find_all("tr"):
				title = tr.find(class_="news-detail-title").a
				idx = self.GET_ID.match(title.attrs["href"]).group(1)
				name = title.string.strip()
				date = tr.find(class_="date").string.strip()
				tag = tr.find(class_="tag").string.strip()
				articles.append((idx, name, date, tag))
			#endfor
			return articles
		#endwith
		return None
	#enddef

	def pull_dates(self, article, check, post_date):
		for x in article.find_all(class_="notice"):
			sis = previous_sibling(x)
			if sis is None or not sis.string: continue
			if check in sis.string.lower():
				# Definitely an event.
				start_date, end_date = x.string.split("-")

				start_offset = timedelta(7/24) if "maintenance" in start_date.lower() else timedelta(0)
				end_offset = timedelta(7/24) if "maintenance" in end_date.lower() else timedelta(0)

				start_date = self.MONTH_DAY.search(start_date).group(0)
				end_date = self.MONTH_DAY.search(end_date).group(0)

				start_date = add_year(start_date, post_date) + start_offset
				end_date = add_year(end_date, post_date) + end_offset

				return start_date, end_date
			#endif
		#endfor

		return None
	#enddef

	def guess_name(self, title):
		# TODO: drop the/a if it's the first word in the name
		sets = [x.group(0) for x in self.SHOP_TITLE.finditer(title)]
		sets.sort(key=lambda x: -len(x.split()))
		return sets[0]
	#enddef

	def fetch_article(self, idx, force=False):
		if not force and idx in self.known:
			return self.known[idx]
		#endif

		with urllib.request.urlopen(self.URL_ALL_ARTICLE % idx) as response:
			html = response.read()

			page = BeautifulSoup(html, "lxml")

			article = page.find(id="news-content")

			name = article.find("h2").string.strip()
			tag = article.find(class_="tag").string.strip()
			post_date = article.find(class_="date").string.strip()
			post_date = dateutil.parser.parse(post_date)
			start_date, end_date = None, None
			args = []

			if tag == "MAINT" or "maintenance" in name.lower():
				times = article.find(class_="fwb").parent
				date_string = times.h4.string.strip()
				maint_date = self.MONTH_DAY.search(date_string).group(0)
				maint_times = {}
				for x in next_sibling(times.h4).find_all("strong"):
					tz = self.GET_TZ.match(x.string.strip()).group(1)
					window = tuple(y.strip() for y in x.next_sibling.string.strip().strip(":").split("-"))
					maint_times[tz] = window
				#endfor

				pacific = maint_times.get("PDT", maint_times.get("PST"))
				start_date = "%s %s" % (maint_date, pacific[0])
				end_date = "%s %s" % (maint_date, pacific[1])
				
				start_date = dateutil.parser.parse(start_date)
				end_date = dateutil.parser.parse(end_date)

				if end_date < start_date:
					end_date += timedelta(1)
				#endif

				diff = ceil((end_date - start_date).seconds / 3600)

				args = [
					"n" if "unscheduled" in name.lower() else "y",
					"n",
					"y" if "PDT" in maint_times else "n",
					"%i hours" % diff,
					"n"
				]

				post_type = "maint"
				when_post = "1"
			else:
				links = article.find_all(href = self.SHOP_LINK)

				if links:
					# Shop notice.
					dates = self.pull_dates(article, "sale date", post_date)
					post_type = "sale"
					if dates:
						start_date, end_date = dates
						when_post = "2"
					else:
						when_post = "1"
					#endif

					# TODO: If there's only one shop link, grab the title of that item.

					args = [self.guess_name(name)]
				elif "patch note" in name.lower():
					post_type = "patch notes"
					when_post = "0"
				else:
					# Event notice, probably.
					dates = self.pull_dates(article, "event date", post_date)
					if dates:
						# Definitely an event.
						start_date, end_date = dates
						post_type = "event"
						when_post = "2"
					else:
						# ???
						post_type = "unknown"
						when_post = "1"
					#endif
				#endif
			#endif

			data = (name, tag, post_type, post_date, start_date, end_date, when_post, *args)
			self.known[idx] = data
			return data
		#endwith
	#enddef

	def update_known(self):
		for idx, *data in self.fetch_news_list():
			if idx not in self.known:
				self.fetch_article(idx)
			#endif
		#endfor
	#enddef

	## Deal with wiki ##
	def reconnect(self):
		# Whitelist tokens.
		tokens = {}
		for k in ("consumer_token", "consumer_secret", "access_token", "access_secret"):
			tokens[k] = config.tokens[k]
		#endfor

		# Make connection.
		self.wiki = mwclient.Site(self.URL_WIKI_BASE, path=self.URL_WIKI_PATH,
			**tokens)
	#enddef

	def connected(self):
		if self.wiki is None:
			self.reconnect()
		#endif
		return self.wiki
	#enddef

	def find_postable(self):
		now = datetime.now()
		postable = {}
		for idx, (name, tag, post_type, post_date, start_date, end_date, when_post, *args) in self.known.items():
			if when_post == "1":
				date = post_date
			elif when_post == "2":
				date = start_date
			else:
				continue
			#endif

			order = self.TYPE_ORDER.index(post_type)
			if now > date:
				key = date.strftime("%Y-%m-%d")
				postable.setdefault(key, []).append((idx, order))
			#endif
		#endfor
		return postable
	#enddef

	def fetch_wiki_news(self):
		news_page = self.connected().pages[self.URL_WIKI_NEWS]
		text = news_page.text()
		try:
			idx = text.index("<!-- News Start ")
			idx = text.index("-->", idx) + 3
			end = text.index("<!-- News End ")
		except ValueError:
			print("Comment(s) missing!!")
			# TODO: standard logging
		else:
			prefix = text[:idx]
			suffix = text[end:]
			text = text[idx:end].strip()

			news = {}
			for entry in self.WIKI_ENTRY.finditer(text):
				date, items = entry.groups()
				date = self.SUP.sub("", date)
				date = dateutil.parser.parse(date).strftime("%Y-%m-%d")
				news[date] = items.strip().split("\n")
			#endfor

			return news, prefix, suffix
		#endtry
	#enddef

	def build_page(self, current=None, news=None):
		"""
		Fold news into current.
		current is either:
			None - call self.fetch_wiki_news
			dict - Date-indexed (YYYY-MM-DD) dict of lists
			       containing formatted news items.
			(dict, prefix, suffix) - dict same as above,
			       prefix and suffix surround formatted page.
		news is either:
			None - call self.find_postable
			dict - Date-indexed dict of (idx, order)
		"""
		if current is None:
			current, prefix, suffix = self.fetch_wiki_news()
			if prefix: prefix += "\n"
		elif isinstance(current, dict):
			prefix, suffix = "", ""
		elif isinstance(current, tuple):
			current, *affixes = current
			prefix = (affixes[0] + "\n") if len(affixes) >= 1 else ""
			suffix = affixes[1] if len(affixes) >= 2 else ""
		else:
			raise TypeError("current")
		#endif

		if news is None:
			news = self.find_postable()
		elif not isinstance(news, dict):
			raise TypeError("news")
		#endif

		# Give each existing entry a sort order.
		page = {}
		for date, items in current.items():
			news_items = []

			for item in items:
				if not item: continue
				if item[0] == "*":
					order = 2
				else:
					order = 0
				#endif
				news_items.append((item, order))
			#endfor

			page[date] = news_items
		#endfor

		# Fold in the news.
		items_so_far = ""
		new_news = False
		for date, items in reversed(sorted(news.items())):
			day = page.setdefault(date, [])

			items_so_far += "\n".join(x[0] for x in page[date]) + "\n"

			for idx, order in items:
				# Make sure it's not already there.
				print(idx, ",", idx in items_so_far)
				if not idx in items_so_far:
					name, tag, post_type, post_date, start_date, end_date, when_post, *args = self.known[idx]
					sub = "".join(str(int(bool(x))) for x in (start_date, end_date))
					message = self.MESSAGES[post_type]

					if isinstance(message, dict):
						message = message.get(sub, message.get(""))
					#endif

					kwargs = {
						"index": idx,
						"name": name,
						"posted": post_date,
						"posted_o": ordinal(post_date.day),
					}

					if start_date:
						kwargs.update({
							"start": start_date,
							"start_o": ordinal(start_date.day),
						})
					if end_date:
						kwargs.update({
							"end": end_date,
							"end_o": ordinal(end_date.day),
						})
					#endif

					msg = message.format(*args, **kwargs)
					day.append((msg, order))
					new_news = True
				#endif
			#endfor

			day.sort(key=lambda x: x[1])
		#endfor

		if not new_news:
			return None
		#endif

		# Build new page contents.
		new_page = ""
		for date in reversed(sorted(page.keys())):
			parsed = dateutil.parser.parse(date)
			formatted = "{date:%B} {date.day}<sup>{}</sup>, {date:%Y}".format(
				ordinal(parsed.day), date=parsed)
			new_page += "''%s''\n%s\n\n" % (formatted, "\n".join(x[0] for x in page[date]))
		#endfor

		return prefix + new_page + suffix
	#enddef

	def update_wiki(self):
		news = self.find_postable()
		contents = self.build_page(news=news)

		if contents:
			page = self.connected().pages[self.URL_WIKI_NEWS]
			page.save(contents, "Automatically updated news. Check my work please!")
		else:
			# TODO: Proper logging
			print("Nothing to update")
		#endif

		for items in news.values():
			for idx, _ in items:
				data = self.known[idx]
				self.known[idx] = data[0:6] + ("x",) + data[7:]
			#endfor
		#endfor
	#enddef
#endclass

if __name__ == '__main__':
	nn = NexonNews()
	nn.update_known()
	nn.update_wiki()
	nn.save_known()

	# TODO: Update events, sales, and maint banner

	# TODO: Proper logging
	print("Done updating wiki.")
#endif
