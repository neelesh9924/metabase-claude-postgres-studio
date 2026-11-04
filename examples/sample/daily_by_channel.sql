select d::date as "Day",
       c.channel as "Channel",
       (c.base + 40 * sin(extract(doy from d) / 2.0 + c.shift))::int as "Tickets"
from generate_series(current_date - 13, current_date, interval '1 day') as d
cross join (values ('App', 420, 0.0), ('POS machine', 610, 1.5), ('Website', 150, 3.0)) as c(channel, base, shift)
order by 1, 2
