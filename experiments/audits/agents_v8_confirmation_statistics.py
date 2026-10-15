"""Preregistered 48-instance inference; stdlib only, zero provider calls."""
from functools import lru_cache
import math
from statistics import mean,stdev,NormalDist


def quality_lower(k,n):
    if not isinstance(k,int) or not isinstance(n,int) or not 0<=k<=n or n<1:raise ValueError('binomial counts required')
    if k==0:return 0.
    low,high=0.,1.
    for _ in range(70):
        p=(low+high)/2
        tail=sum(math.comb(n,j)*p**j*(1-p)**(n-j) for j in range(k,n+1))
        if tail<.025:low=p
        else:high=p
    return (low+high)/2


def student_cdf(x,df):
    if not isinstance(df,int) or df<1 or not math.isfinite(x):raise ValueError('finite x and positive integer df required')
    if x<0:return 1-student_cdf(-x,df)
    n=2048;step=x/n;coef=math.exp(math.lgamma((df+1)/2)-math.lgamma(df/2)) / math.sqrt(df*math.pi)
    def density(t):return coef*(1+t*t/df)**(-(df+1)/2)
    integral=step/3*(density(0)+density(x)+sum((4 if i%2 else 2)*density(i*step) for i in range(1,n)))
    return .5+integral


@lru_cache(maxsize=None)
def critical_t(df):
    low,high=0.,16.
    for _ in range(55):
        mid=(low+high)/2
        if student_cdf(mid,df)<.95:low=mid
        else:high=mid
    return (low+high)/2


def confirm(rows,complete):
    keys=[(r['task'],r['arm']) for r in rows]
    if len(set(keys))!=len(keys):raise ValueError('duplicate task/arm')
    groups={task:{r['arm']:r for r in rows if r['task']==task} for task in {r['task'] for r in rows}}
    eligible=bool(complete and len(rows)==144 and len(groups)==48 and all(set(g)=={'B0','B1','B2'} for g in groups.values())
                  and all(isinstance(r.get('quality_passed'),bool) for r in rows)
                  and all(isinstance(r.get(k),(int,float)) and not isinstance(r[k],bool) and math.isfinite(r[k]) and r[k]>0 for r in rows for k in ('total_tokens','cost_low','cost_high')))
    result=dict(eligible=eligible,planned_instances=48,observed_instances=len(groups),joint_confirmed=False,
                quality={},endpoints={},assumptions='independent tasks and approximately normal paired log effects; conditional on synthetic family/runtime')
    if not eligible:return result
    for arm in ('B1','B2'):
        k=sum(g[arm]['quality_passed'] for g in groups.values());lower=quality_lower(k,48)
        result['quality'][arm]=dict(passed=k,n=48,success_lower=lower,difference_lower=lower-1,noninferior=lower>=.90)
    for metric in ('total_tokens','cost_low','cost_high'):
        logs=[math.log(g['B1'][metric]/g['B0'][metric]) for _,g in sorted(groups.items())]
        avg=mean(logs);sd=stdev(logs);upper=avg+critical_t(47)*sd/math.sqrt(48)
        result['endpoints'][metric]=dict(n=48,mean_log_ratio=avg,log_sd=sd,geomean_ratio=math.exp(avg),ratio_upper=math.exp(upper),at_least_10pct_lower=upper<=math.log(.90))
    result['joint_confirmed']=all(v['noninferior'] for v in result['quality'].values()) and all(v['at_least_10pct_lower'] for v in result['endpoints'].values())
    return result


def sensitivity():
    z=NormalDist().inv_cdf(.95)+NormalDist().inv_cdf(.8)
    ns={f'sigma{sigma}_ratio{ratio}':math.ceil((z*sigma/math.log(.90/ratio))**2) for sigma in (.2,.3,.5) for ratio in (.75,.8,.85)}
    return dict(normal_approx_cost_n=ns,quality_all48_pass_probability={str(p):p**48 for p in (.95,.98,.99,.995)},
                quality_lower_all48=quality_lower(48,48),quality_lower_47of48=quality_lower(47,48),critical_t_df47=critical_t(47),
                fixed_n=48,joint_power_guaranteed=False,adaptive_expansion_allowed=False)
