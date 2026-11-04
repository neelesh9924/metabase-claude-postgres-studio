select d::date as "Day",
       (412000 + 9000 * (extract(day from d)::int % 7)) as "Revenue"
from generate_series(current_date - 6, current_date, interval '1 day') as d
order by 1
