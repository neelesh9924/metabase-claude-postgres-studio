select 'Operator ' || chr(64 + n) as "Operator",
       (current_date - n) as "Last trip",
       (1200 - 85 * n) as "Tickets",
       round((1200 - 85 * n) * 348.5) as "Revenue"
from generate_series(1, 8) as n
order by 3 desc
