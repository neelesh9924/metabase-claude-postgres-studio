select d::date as "Day",
       (640 - 11 * (extract(day from d)::int % 5)) as "Trips"
from generate_series(current_date - 6, current_date, interval '1 day') as d
order by 1
