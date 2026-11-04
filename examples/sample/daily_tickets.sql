select d::date as "Day",
       (900 + 180 * sin(extract(doy from d) / 3.0) + 12 * extract(dow from d))::int as "Tickets"
from generate_series(current_date - 29, current_date, interval '1 day') as d
order by 1
