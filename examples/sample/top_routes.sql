select route as "Route", tickets as "Tickets"
from (values ('Delhi - Jaipur', 1840), ('Lucknow - Kanpur', 1525), ('Agra - Delhi', 1310),
             ('Jaipur - Ajmer', 990), ('Bareilly - Delhi', 760), ('Meerut - Haridwar', 540)) as t(route, tickets)
order by 2 desc
