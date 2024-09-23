#!/usr/bin/env python3

import os
import re
import csv
import json
import logging
from math import ceil
from datetime import date, datetime, timedelta
from itertools import chain
from collections import defaultdict

import mwclient
import requests
import dateutil.tz
import dateutil.parser
from bs4 import BeautifulSoup, element as bs4_element

import config

logger = logging.getLogger("newscast")
logger.setLevel(logging.INFO)
logstream = logging.StreamHandler()
logstream.setLevel(logging.INFO)
logger.addHandler(logstream)

os.makedirs("news", exist_ok=True)
os.makedirs("shop", exist_ok=True)

tz_pacific = dateutil.tz.gettz("America/Los_Angeles")
tzinfos = {
	"PDT": tz_pacific,
	"PST": tz_pacific
}

def offset_year(date, offet):
	return datetime(date.year + offet, date.month, date.day,
		date.hour, date.minute, date.second, date.microsecond)
#enddef

def add_year(date, posted=datetime.now()):
	if isinstance(posted, str):
		posted = dateutil.parser.parse(posted)
		posted = datetime(posted.year, posted.month, posted.day)
	#endif

	date = dateutil.parser.parse(date, tzinfos=tzinfos)
	if not date.tzinfo:
		date = date.astimezone(tz_pacific)

	if date < posted:
		return offset_year(date, 1)
	else:
		return date
	#endif
#enddef

def add_year_range(start, end):
	start = dateutil.parser.parse(start, tzinfos=tzinfos)
	try: end = dateutil.parser.parse(end, tzinfos=tzinfos)
	except: return start, end

	if start < end:
		return start, end
	else:
		now = datetime.now()

		if now < start:
			return offset_year(start, -1), end
		else:
			return start, offset_year(end, 1)
		#endif
	#endif
#enddef

def toISO(date: datetime, tz="Z"):
	return date.astimezone(dateutil.tz.UTC).isoformat().replace("+00:00", tz)

def get(url):
	return requests.get(url, headers={"X-MB-API-KEY": config.X_MB_API_KEY})

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
	URL_ALL = "https://g.nexonstatic.com/mabinogi/cms/news"
	URL_ALL_ARTICLE ="https://g.nexonstatic.com/mabinogi/cms/news/{}"
	URL_SHOP_ITEM = "https://mabinogi.nexon.net/api/shop/itemdetail/cash/{}"
	URL_WIKI_BASE = "wiki.mabinogiworld.com"
	URL_WIKI_PATH = "/"
	URL_WIKI_NEWS = "Wiki_Home/WikiUpdates"
	URL_WIKI_MAINT = "Wiki_Home/Maintenance_Notice"
	URL_WIKI_EVENTS = "Wiki_Home/Current_Events"
	URL_WIKI_SALES = "Wiki_Home/Current_Sales"

	GET_ID = re.compile(r'/news/(\d+)')
	GET_TZ = re.compile(r'.*?\(([^,]+)')
	SHOP_LINK = re.compile(r'/shop/webshop/detail/cash/(\d+)')
	SHOP_TITLE = re.compile(r"([A-Z][0-9a-zA-Z'-]*\b(\s+|$|[!?]))+")
	MONTH_DAY = re.compile(r'([a-zA-Z]{3,}) (\d+)')
	WIKI_ENTRY = re.compile(r"''([^']+)''(.*?)(?=^''|\Z)", re.M | re.S)
	SUP = re.compile(r'<sup>.*?</sup>', re.I)
	WIKI_LINK = re.compile(r'\[\[(?:([^|\]]+)\|)?([^\]]+)\]\]')
	BAD_IN_WIKI_LINK = re.compile(r'\[.*?\]|[\[\]\|]')
	ITEM_COUNT = re.compile(r'\s*\(\d+\)$')

	KNOWN_FILE = "known.csv"

	TYPE_ORDER = [
		"maint",
		"update",
		"event",
		"*",
		"sale",
		"unknown",
		"art corner",
	]
	# index = post index
	# name = post title
	# posted = date of nexon's post
	# start = starting datetime of sale/event/maint
	# end = ending datetime of sale/event/maint
	MESSAGES = {
		"maint": "{{{{:Wiki Home/Maintenance (new)|isScheduled={}|isUpdate={}|startUTC={start_iso}|endUTC={end_iso}|length={}|src={index}|ended={}}}}}",
		"event": "*The [[{}]]{} has started. For more information, see [https://mabinogi.nexon.net/news/{index} here.]",
		"sale": {
			"00": "*The [[{}]]{} is now available for a limited time from ??? to ???. For more information, see [https://mabinogi.nexon.net/news/{index} here]",
			"01": "*The [[{}]]{} is now available for a limited time from ??? to {end:%B} {end.day}<sup>{end_o}</sup>. For more information, see [https://mabinogi.nexon.net/news/{index} here.]",
			"10": "*The [[{}]]{} is now available for a limited time from {start:%B} {start.day}<sup>{start_o}</sup> to ???. For more information, see [https://mabinogi.nexon.net/news/{index} here.]",
			"11": "*The [[{}]]{} is now available for a limited time from {start:%B} {start.day}<sup>{start_o}</sup> to {end:%B} {end.day}<sup>{end_o}</sup>. For more information, see [https://mabinogi.nexon.net/news/{index} here.]",
		},
		"update": "*The {}{} has been announced. For more information, see [https://mabinogi.nexon.net/news/{index} here.]",
		"art corner": "*The art corner for {posted:%B} is up! Check out the featured artists [https://mabinogi.nexon.net/news/{index} here.]",
		"unknown": "*[https://mabinogi.nexon.net/news/{index} {name_safe}] (Please add details.)",
	}

	MAINT_TEMPLATE = (
		"{{{{Maintenance Notice\n"
		"|from={start:%Y-%m-%d %I:%M:%S %p}\n"
		"|until={end:%Y-%m-%d %I:%M:%S %p}\n"
		"}}}}"
	)

	CURRENT_TEMPLATE = re.compile(r"^\|-\n\|(.*)\n\|(.*)\n\|(.*)", re.M)

	def __init__(self):
		# idx: name, tag, type, post date, start date, end date, when to post, *other info
		# when to post
		#  0 - don't post
		#  1 - immediately (post date)
		#  2 - delayed (start date)
		#  x - posted to news, needs to be posted to current
		#  y - posted to current
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
					try: idx, name, tag, post_type, post_date, start_date, end_date, when_post, *args = line
					except:
						print(line)
						raise
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
				if post_date: post_date = toISO(post_date)
				if start_date: start_date = toISO(start_date)
				if end_date: end_date = toISO(end_date)
				writer.writerow([idx, name, tag, post_type, post_date, start_date, end_date, when_post, *args])
			#endfor
		#endwith
	#enddef

	## Download news ##
	def fetch_news_list(self):
		data = get(self.URL_ALL).json()
		articles = []
		for article in data:
			idx = str(article["Id"])
			name = article["Title"]
			date = article["LiveDate"]
			tag = article["Category"]
			articles.append((idx, name, date, tag))
		return articles
	#enddef

	def pull_dates(self, article, check, post_date):
		for x in article.find_all(class_="notice"):
			sis = None
			for _ in range(3):
				for y in x.previous_siblings:
					if isinstance(y, bs4_element.NavigableString):
						if check in str(y).lower():
							sis = y
							break
					elif check in y.getText().lower():
						sis = y
						break
				if sis is not None: break
				x = x.parent
			if "-" not in (x.string or ""):
				for y in x.children:
					if "-" in (y.string or ""):
						x = y
						break
			if sis is not None and "-" in (x.string or ""):
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

	def guess_name(self, title: str):
		title = self.BAD_IN_WIKI_LINK.sub("", title).strip()
		ltitle = title.lower()
		# Drop stuff that can combine with others..
		if ltitle.startswith("return of "):
			title = title[10:]
			ltitle = ltitle[10:]

		# Drop the/a if it's the first word in the name
		if ltitle.startswith("the "):
			title = title[4:]
		elif ltitle.startswith("a "):
			title = title[2:]
		elif ltitle.startswith("shopkeeper's sale: "):
			title = title[19:]

		ends = ["returns", "is back", "preview"]
		for x in [y for x in ends for y in [f" {x}", f" {x}!"]]:
			if ltitle.endswith(x):
				title = title[:-len(x)]
				break

		return title
	#enddef

	def fetch_article(self, idx, force=False):
		if not force and idx in self.known:
			return self.known[idx]
		#endif

		article = get(self.URL_ALL_ARTICLE.format(idx)).json()
		
		with open(f"news/{idx}.json", "w") as f:
			json.dump(article, f, indent="\t")

		page = BeautifulSoup(article["Body"], "lxml")

		name = article["Title"]
		lname = name.lower()
		tag = article["Category"]
		post_date = dateutil.parser.parse(article["LiveDate"])
		start_date, end_date = None, None
		args = []

		if "patch note" in lname:
			# In patch notes, the first ul contains a list of events and stuff
			# For now, pass on these to not do dupes. Decide later if they
			# should be preferred over scanning everything separately.
			post_type = "patch notes"
			when_post = "0"
		elif (tag == "maintenance" or "maintenance" in lname) and "launcher" not in name.lower():
			element = None
			for x in chain(page.find_all("strong"), page.find_all("h4")):
				mo = self.MONTH_DAY.search(x.getText())
				if mo:
					maint_date = f"{mo.group(0)}, {post_date.year}"
					try:
						test_date = dateutil.parser.parse(f"{maint_date} 11:59:59 PST", tzinfos=tzinfos)
					except dateutil.parser.ParserError:
						continue
					element = x
					if test_date < post_date:
						maint_date = f"{mo.group(0)} {post_date.year+1}"
					break

			if element is None:
				post_type = "unknown"
				when_post = "1"
			else:
				maint_times = {}
				time_elem = None
				while time_elem is None:
					time_elem = next_sibling(element)
					element = element.parent

				time_lines = "".join([
					("\n" if x.name == "br" else x.getText())
					if isinstance(x, bs4_element.Tag) else str(x)
					for x in time_elem.children
				]).split("\n")
				for text in time_lines:
					mo = self.GET_TZ.match(text)
					if not mo:
						continue
					tz = mo.group(1)
					start, end = tuple(y.strip() for y in text.split(":", 1)[1].split("-"))
					start = f"{maint_date} {start}"
					if "," in end:
						# TODO: naive but not sure what else to do right now
						# Date form example: 10:00 AM, Tuesday, June 4th
						ends = end.split(",")
						end = f"{ends[-1].strip()} {ends[0].strip()}"
					else:
						end = f"{maint_date} {end}"
					maint_times[tz] = (start, end)
				#endfor

				start = None
				for x, y in [("PST", "PDT"), ("PDT", "PST")]:
					if x in maint_times:
						tz = x
						start, end = maint_times[x]
						# These happen in DST maint posts
						if tz not in start:
							if y in start:
								tz = y
							else:
								start = f"{start} {tz}"
						if "PST" not in end and "PDT" not in end:
							end = f"{end} {tz}"

				if start is None:
					post_type = "unknown"
					when_post = "1"
				else:
					start_date = dateutil.parser.parse(start, tzinfos=tzinfos)
					end_date = dateutil.parser.parse(end, tzinfos=tzinfos)

					if end_date < start_date:
						# Overnight maints
						end_date += timedelta(1)
					#endif

					diff = ceil((end_date - start_date).seconds / 3600)

					args = [
						"n" if "unscheduled" in name.lower() else "y",
						"y" if any(
							x.getText().lower().endswith("update")
							for x in page.find_all("a")
						) else "n",
						"%i hours" % diff,
						"y" if "complete" in lname else "n"
					]

					post_type = "maint"
					when_post = "1"
		elif tag == "updates":
			# Not Patch Notes
			update_name = self.guess_name(name).split(" - ")[0].strip()
			update_suffix = ""
			if not update_name.lower().endswith(" update"):
				update_suffix = " update"
			args = [update_name, update_suffix]
			post_type = "update"
			when_post = "1"
		elif tag == "sales":
			# Shop notice.
			try:
				dates = self.pull_dates(page, "sale date", post_date)
			except:
				print(f"Error pulling dates from {idx}")
				raise

			links={
				x["href"]
				for x in page.find_all(href = self.SHOP_LINK)
			}
			titles = set()
			for x in links:
				shop_idx = self.SHOP_LINK.search(x).group(1)
				url = self.URL_SHOP_ITEM.format(shop_idx)
				res = get(url)
				if res.status_code >= 400:
					continue
				ret = res.json()
				title = ret["Item"]["ProductTitle"]
				titles.add(self.ITEM_COUNT.sub("", title))

				with open(f"shop/{shop_idx}.json", "w") as f:
					json.dump(ret, f)

			sale_name = (
				titles.pop()
				if len(titles) == 1 else
				self.guess_name(name).rstrip("!").split(" - ")[0]
			)
			post_type = "sale"
			if dates:
				start_date, end_date = dates
				when_post = "2"
			else:
				when_post = "1"
			#endif

			sale_suffix = ""
			if "shopkeeper's sale" in lname:
				sale_suffix = " sale"
			args = [sale_name, sale_suffix]
		elif tag == "events":
			# Event notice
			dates = self.pull_dates(page, "event date", post_date)
			post_type = "event"
			if dates:
				start_date, end_date = dates
				when_post = "2"
			else:
				when_post = "1"
			#endif
			event_name = self.guess_name(name)
			event_suffix = ""
			if not event_name.lower().endswith(" event"):
				event_suffix = " event"
			args = [event_name, event_suffix]
		elif "art corner" in lname:
			post_type = "art corner"
			when_post = "1"
		else:
			# ???
			post_type = "unknown"
			when_post = "1"
		#endif

		data = (name, tag, post_type, post_date, start_date, end_date, when_post, *args)
		self.known[idx] = data
		return data
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
		now = datetime.now(tz_pacific)
		postable = defaultdict(list)
		for idx, (name, tag, post_type, post_date, start_date, end_date, when_post, *args) in self.known.items():
			if when_post == "1":
				date = post_date
			elif when_post == "2":
				date = start_date
			else:
				continue
			#endif

			order = self.TYPE_ORDER.index(post_type)
			if date.tzinfo is None:
				#print(f"bad date for {idx} '{name}': {date}")
				date = date.astimezone(dateutil.tz.UTC)
			if now > date:
				key = date.strftime("%Y-%m-%d")
				postable[key].append((idx, order))
			#endif
		#endfor
		return postable
	#enddef

	def partition_page(self, text, name):
		try:
			idx = text.index("<!-- {} Start ".format(name))
			idx = text.index("-->", idx) + 3
			if text[idx] == "\n": idx += 1
			end = text.index("<!-- {} End ".format(name), idx)
			if text[end-1] == "\n": end -= 1
		except ValueError:
			logger.error("Comment(s) missing!!")
		else:
			prefix = text[:idx]
			suffix = text[end:]
			text = text[idx:end].strip()

			return prefix, text, suffix
		#endtry
	#enddef

	def fetch_wiki_news(self):
		news_page = self.connected().pages[self.URL_WIKI_NEWS]
		text = news_page.text()

		partitions = self.partition_page(text, "News")
		if not partitions: return
		prefix, text, suffix = partitions

		news = {}
		for entry in self.WIKI_ENTRY.finditer(text):
			date, items = entry.groups()
			date = self.SUP.sub("", date)
			date = dateutil.parser.parse(date).strftime("%Y-%m-%d")
			news[date] = items.strip().split("\n")
		#endfor

		return news, prefix, suffix
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
		elif isinstance(current, dict):
			prefix, suffix = "", ""
		elif isinstance(current, tuple):
			current, *affixes = current
			prefix = affixes[0] if len(affixes) >= 1 else ""
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
						"name_safe": self.BAD_IN_WIKI_LINK.sub("", name),
						"posted": post_date,
						"posted_o": ordinal(post_date.day),
					}

					if start_date:
						kwargs.update({
							"start": start_date,
							"start_o": ordinal(start_date.day),
							"start_iso": toISO(start_date, ""),
						})
					if end_date:
						kwargs.update({
							"end": end_date,
							"end_o": ordinal(end_date.day),
							"end_iso": toISO(end_date, ""),
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
			parsed_o = ordinal(parsed.day)
			formatted = f"{parsed:%B} {parsed.day}<sup>{parsed_o}</sup>, {parsed:%Y}"
			content = "\n".join(x[0] for x in page[date])
			if content.strip():
				new_page += f"''{formatted}''\n{content}\n\n"
		#endfor

		return prefix + new_page.strip() + suffix
	#enddef

	def update_wiki(self):
		news = self.find_postable()
		contents = self.build_page(news=news)

		if contents:
			page = self.connected().pages[self.URL_WIKI_NEWS]
			page.save(contents, "Automatically updated news. Check my work please!")
		else:
			logger.info("Nothing to update")
		#endif

		for items in news.values():
			for idx, _ in items:
				data = self.known[idx]
				self.known[idx] = data[0:6] + ("x",) + data[7:]
			#endfor
		#endfor
	#enddef

	def get_upcoming(self, want_type, started=False):
		now = datetime.now().astimezone(dateutil.tz.UTC)
		ret = []
		for idx, (name, tag, post_type, post_date, start_date, end_date, when_post, *args) in self.known.items():
			if post_type == want_type and when_post == "x":
				if end_date and end_date > now and (not started or (start_date and start_date < now)):
					ret.append((idx, end_date))
				#endif
			#endif
		#endfor

		return [x[0] for x in sorted(ret, key=lambda x: x[1])]
	#enddef

	def update_maint(self):
		maints = self.get_upcoming("maint")

		if not maints:
			return
		#endif

		idx = maints[0]
		name, tag, post_type, post_date, start_date, end_date, when_post, *args = self.known[idx]

		if not (start_date and end_date):
			return
		#endif

		contents = self.MAINT_TEMPLATE.format(
			start=start_date.astimezone(tz_pacific),
			end=end_date.astimezone(tz_pacific)
		)
		page = self.connected().pages[self.URL_WIKI_MAINT]
		page.save(contents, "Automatically updated notice. Check my work please!")
	#enddef

	def fetch_current(self, text):
		current = []
		for start, end, link in self.CURRENT_TEMPLATE.findall(text):
			name = self.WIKI_LINK.search(link)
			if not name: continue
			start, end = add_year_range(start, end)
			current.append((start, end, name.group(2), name.group(1), None))
		#endfor
		return current
	#enddef

	def fold_in_current(self, current, want_type):
		added = False
		names = {name.lower() for start, end, name, name2, idx in current}
		for idx in self.get_upcoming(want_type, True):
			name, tag, post_type, post_date, start_date, end_date, when_post, *args = self.known[idx]
			if post_type in ("event", "sale"):
				name = args[0]
			name = self.BAD_IN_WIKI_LINK.sub("", name)
			if name.lower() not in names:
				# TODO: This is naive; check if page exists
				current.append((start_date, end_date, name, name, idx))
				added = True
			#endif
		#endfor
		return added
	#enddef

	def build_current(self, url, want_type):
		page = self.connected().pages[url]

		partitions = self.partition_page(page.text(), "List")
		if not partitions: return None, None
		prefix, text, suffix = partitions

		now = datetime.now()
		current = [(start, end, *args) for start, end, *args in self.fetch_current(text) if isinstance(end, str) or end > now]
		if not self.fold_in_current(current, want_type):
			logger.info(f"Nothing to update in current {want_type}s")
			return None, None
		#endif

		contents = []
		added = set()
		for start, end, name, link, idx in sorted(current, key=lambda x: x[2]):
			if name == link or link is None:
				link = "[[{}]]".format(name)
			else:
				link = "[[{}|{}]]".format(link, name)
			#endif
			contents.append("|-\n|{start:%b} {start.day}\n|{end:%b} {end.day}\n|{link}".format(
				start=start, end=end, link=link))
			added.add(idx)
		#endfor

		return prefix + "\n".join(contents) + suffix, added
	#enddef

	def update_current(self, url, want_type):
		contents, added = self.build_current(url, want_type)

		if contents:
			page = self.connected().pages[url]
			page.save(contents, "Automatically updated current {}s. Check my work please!".format(want_type))
		else:
			logger.info("Nothing to update")
			return
		#endif

		added.remove(None)
		for idx in added:
			data = self.known[idx]
			self.known[idx] = data[0:6] + ("y",) + data[7:]
		#endfor
	#enddef
#endclass

if __name__ == '__main__':
	nn = NexonNews()
	nn.update_known()

	nn.update_wiki()

	# Update events, sales, and maint banner
	nn.update_maint()
	nn.update_current(NexonNews.URL_WIKI_EVENTS, "event")
	nn.update_current(NexonNews.URL_WIKI_SALES, "sale")

	nn.save_known()
	logger.info("Done updating wiki.")
#endif
