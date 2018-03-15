from importFile import *
from pyomo.bilevel import *
from microgrid_Model import *
import pandas as pd
import copy
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
H2M = 0.2
def DayAheadModel(microgrid_data,case,T_range):
    microgrid_device = case.device
    N_T = case.NumOfTime
    T = range(len(T_range))
    step = 24 / N_T
    N_es = case.getKey(electricStorage)
    N_absc = case.getKey(absorptionChiller)
    N_bol = case.getKey(boiler)
    N_cs = case.getKey(coldStorage)
    N_ac = case.getKey(airConditioner)
    N_gt = case.getKey(gasTurbine)
    N_pv = case.getKey(PV)
    acLoad = microgrid_data['交流负荷'][T[0]:T[-1]+1].tolist()
    dcLoad = microgrid_data['直流负荷'][T[0]:T[-1]+1].tolist()
    pv_output = microgrid_data['光伏出力'][T[0]:T[-1]+1].tolist()
    microgrid_device['ut'].buy_price = microgrid_data['电价'][T[0]:T[-1]+1].tolist()
    cold_load = microgrid_data['冷负荷'][T[0]:T[-1]+1].tolist()
    water_heat_load = microgrid_data['热水负荷'][T[0]:T[-1]+1].tolist()
    steam_heat_load = microgrid_data['蒸汽负荷'][T[0]:T[-1]+1].tolist()
    '''A general model and algorithm for microgrid optimal dispatch'''
    '''define sets'''
    optimalDispatch = ConcreteModel(name='IES_optimalDispatch')
    wind_power_max = microgrid_data['风机出力上限'][T[0]:T[-1]+1].tolist()
    wind_power_min = microgrid_data['风机出力下限'][T[0]:T[-1] + 1].tolist()
    optimalDispatch.wp = Var(T,bounds=lambda mdl,t: (0, 1))
    '''This is the sub-problem'''
    optimalDispatch.sub = SubModel()
    optimalDispatch.sub.T = T
    optimalDispatch.sub.T_range = T_range
    optimalDispatch.sub.input = microgrid_data
    optimalDispatch.sub.case = case
    '''define variables'''
    # electrical storage
    optimalDispatch.sub.es_power = Var(N_es, T, bounds=lambda mdl, i, T: (-microgrid_device[i].Pmax_in, microgrid_device[i].Pmax_out))
    optimalDispatch.sub.es_energy = Var(N_es, T, bounds=lambda mdl, i, T: (
    microgrid_device[i].SOCmin * microgrid_device[i].capacity,
    microgrid_device[i].SOCmax * microgrid_device[i].capacity))
    # absorption chiller
    optimalDispatch.sub.absc_heat_in = Var(N_absc, T, bounds=lambda mdl, i, T: (0, microgrid_device[i].Hmax))
    # heat variables
    optimalDispatch.sub.buy_heat = Var(T,bounds = (0,microgrid_device['ut'].PCC['maxH']))
    optimalDispatch.sub.medium_heat = Var(T,bounds=(-10000,10000))
    optimalDispatch.sub.low_heat = Var(T,bounds=(-10000,10000))
    # boiler
    optimalDispatch.sub.bol_power = Var(N_bol, T)
    optimalDispatch.sub.bol_constraint1 = Constraint(N_bol,T,rule = lambda  mdl,i,t: mdl.bol_power[i,t] <= microgrid_device[i].Pmax)
    optimalDispatch.sub.bol_constraint2 = Constraint(N_bol, T,rule=lambda mdl, i, t: mdl.bol_power[i, t] >= microgrid_device[i].Pmin)
    # cold storage
    optimalDispatch.sub.cs_cold = Var(N_cs, T, bounds=lambda mdl, i, T: (-microgrid_device[i].Hin, microgrid_device[i].Hout))
    optimalDispatch.sub.cs_cold_stored = Var(N_cs, T, bounds=lambda mdl, i, T: (
    microgrid_device[i].Tmin * microgrid_device[i].capacity, microgrid_device[i].Tmax * microgrid_device[i].capacity))
    # air conditioner
    optimalDispatch.sub.ac_power = Var(N_ac, T, bounds=lambda mdl, i, T: (0, microgrid_device[i].Pmax))
    # gas turbine
    optimalDispatch.sub.gt_power = Var(N_gt, T)
    optimalDispatch.sub.gt_constraint1 = Constraint(N_gt,T,rule = lambda mdl,i,t: mdl.gt_power[i,t] <= microgrid_device[i].Pmax)
    optimalDispatch.sub.gt_constraint2 = Constraint(N_gt, T,rule=lambda mdl, i, t: mdl.gt_power[i, t] >= microgrid_device[i].Pmin)
    # inverter
    optimalDispatch.sub.inv_dc = Var(T)  # inv_dc > 0 means energy flows from inverter to dc side
    # utility power
    optimalDispatch.sub.utility_power = Var(T, bounds=(0,10000))
    '''define constraints'''
    '''电功率平衡约束'''

    def ACPowerBalance(mdl, t):
        power_supply = sum(mdl.gt_power[i, t] for i in N_gt) \
                       + mdl.utility_power[t] + optimalDispatch.wp[t]
        power_demand = 1.05*sum(mdl.ac_power[i, t] for i in N_ac) \
                       + acLoad[t] + (1 / microgrid_device['inv'].ac_dc_efficiency) * mdl.inv_dc[t]\
					   + sum(microgrid_device[i].ElecCost * mdl.absc_heat_in[i, t] for i in N_absc)
        return power_supply == power_demand

    def DCPowerBalance(mdl, t):
        power_supply = sum(mdl.es_power[i, t] for i in N_es) + mdl.inv_dc[t] + pv_output[t]
        power_demand = dcLoad[t]
        return power_supply == power_demand

    optimalDispatch.sub.ACPowerBalance = Constraint(T, rule=ACPowerBalance)
    optimalDispatch.sub.DCPowerBalance = Constraint(T, rule=DCPowerBalance)
    '''热功率平衡约束'''
    H2M = 0.2

    optimalDispatch.sub.HPB1 = Constraint(T, rule=lambda mdl, t: mdl.medium_heat[t] == mdl.buy_heat[t] + sum(
        mdl.bol_power[n_bol, t] for n_bol in N_bol) + sum(mdl.gt_power[n_gt, t] * microgrid_device[n_gt].HER * microgrid_device[n_gt].heat_recycle for n_gt in N_gt))
    optimalDispatch.sub.HPB2 = Constraint(T,rule = lambda mdl,t:mdl.medium_heat[t] >= steam_heat_load[t])
    optimalDispatch.sub.HPB3 = Constraint(T, rule=lambda mdl, t: mdl.low_heat[t] == (H2M) * steam_heat_load[t])
    optimalDispatch.sub.HPB4 = Constraint(T, rule=lambda mdl, t: mdl.low_heat[t] + mdl.medium_heat[t] >= water_heat_load[t] + steam_heat_load[t] + sum(mdl.absc_heat_in[n_absc, t] for n_absc in N_absc))
    # TODO 完善高中低品味热模型
    '''冷功率平衡约束'''

    def coldPowerBalance(mdl, t):
        cold_supply = sum(mdl.ac_power[i, t] * microgrid_device[i].EER for i in N_ac) \
                      + sum(mdl.cs_cold[i, t] for i in N_cs) \
                      + sum(mdl.absc_heat_in[i, t] * microgrid_device[i].COP_htc for i in N_absc)
        cold_demand = cold_load[t]
        return cold_supply == cold_demand

    optimalDispatch.sub.coldPowerBalance = Constraint(T, rule=coldPowerBalance)
    '''电池日平衡约束、自放电率、爬坡率约束'''

    def batteryEnergyBalance(mdl, n_es, t):
        bat = microgrid_device[n_es]
        if t == T[0]:
            return mdl.es_energy[n_es, t] == bat.SOCnow * bat.capacity
        else:
            return mdl.es_energy[n_es, t] == mdl.es_energy[n_es, t - 1] * (1 - bat.selfRelease) \
                                             - step * mdl.es_power[n_es, t - 1]

    optimalDispatch.sub.batteryEnergyBalance = Constraint(N_es, T, rule=batteryEnergyBalance)
    optimalDispatch.sub.batteryEnergyBalance0 = Constraint(N_es, rule=lambda mdl,n:mdl.es_energy[n, T[-1]]* (1 - microgrid_device[n].selfRelease) \
                                             - step * mdl.es_power[n, T[-1]] == microgrid_device[n].SOCint * microgrid_device[n].capacity )


    '''冰蓄冷日平衡约束、自放冷率、爬坡率约束'''

    def coldStorageEnergyBalance(mdl, n_cs, t):
        ice = microgrid_device[n_cs]
        if t == 0:
            return mdl.cs_cold_stored[n_cs, t] == ice.Tint * ice.capacity
        else:
            return mdl.cs_cold_stored[n_cs, t] == mdl.cs_cold_stored[n_cs, t - 1] * (1 - ice.selfRelease) \
                                                  - step * mdl.cs_cold[n_cs, t - 1]

    optimalDispatch.sub.coldStorageEnergyBalance = Constraint(N_cs, T, rule=coldStorageEnergyBalance)
    optimalDispatch.sub.coldStorageEnergyBalance0 = Constraint(N_cs, rule=lambda mdl,n:mdl.cs_cold_stored[n, T[-1]]* (1 - microgrid_device[n].selfRelease) \
                                             - step * mdl.cs_cold[n, T[-1]] == microgrid_device[n].capacity*microgrid_device[n].Tint)
    '''燃气轮机/锅炉爬坡率约束'''
    def gtRampLimit(mdl,n,t):
        if t == 0:
            return Constraint.Skip
        else:
            return -microgrid_device[n].maxDetP <= mdl.gt_power[n,t] - mdl.gt_power[n,t-1] <= microgrid_device[n].maxDetP
    def bolRampLimit(mdl,n,t):
        if t == 0:
            return Constraint.Skip
        else:
            return -microgrid_device[n].maxDetP <= mdl.bol_power[n,t] - mdl.bol_power[n,t-1] <= microgrid_device[n].maxDetP
    optimalDispatch.sub.gtRampLimit = Constraint(N_gt,T,rule=gtRampLimit)
    optimalDispatch.sub.bolRampLimit = Constraint(N_bol,T,rule=bolRampLimit)
    '''Define Objectives'''

    def OM_Cost(mdl):
        om_cost = 0
        for id in microgrid_device.keys():
            if (id in N_absc):
                om_cost += microgrid_device[id].om * step * sum(mdl.absc_heat_in[id, t] for t in T)
            if (id in N_bol):
                om_cost += microgrid_device[id].om * step * sum(mdl.bol_power[id, t] for t in T)
            if (id in N_ac):
                om_cost += microgrid_device[id].om * step * sum(mdl.ac_power[id, t] for t in T)
        return om_cost

    def Fuel_Cost(mdl):
        fuel_cost = 0
        for id in N_gt:
            fuel_cost += (1 / microgrid_device[id].efficiency) * microgrid_device['ut'].gas_price * step * sum(
                mdl.gt_power[id, t] for t in T)
        for id in N_bol:
            fuel_cost += (1 / microgrid_device[id].efficiency) * microgrid_device['ut'].gas_price * step * sum(
                mdl.bol_power[id, t] for t in T)
        return fuel_cost

    def ElectricalFee(mdl):
        return step * sum(mdl.utility_power[t] * microgrid_device['ut'].buy_price[t] for t in T)

    def HeatFee(mdl):
        return step * sum(mdl.buy_heat[t] for t in T) * microgrid_device['ut'].steam_price
    def obj_Economical(mdl):
        return OM_Cost(mdl) + Fuel_Cost(mdl) + ElectricalFee(mdl) + HeatFee(mdl)
    def obj_Efficiency(mdl):
        return (Fuel_Cost(mdl)/2.3 * 1.2143 + 0.1229 * 0.25 *sum(mdl.utility_power[t] for t in mdl.T) + 3.6 * 0.3412 * 0.25 * sum(mdl.buy_heat[t] for t in mdl.T)) \
               / (sum(acLoad)+sum(dcLoad)+sum(cold_load)+sum(water_heat_load)+sum(steam_heat_load))
    optimalDispatch.sub.obj_Economical = obj_Economical
    optimalDispatch.sub.obj_Efficiency = obj_Efficiency
    optimalDispatch.sub.objective = Objective(rule=obj_Economical)
    optimalDispatch.objective = Objective(rule=lambda mdl: sum(mdl.wp[t] for t in T))
    return optimalDispatch
def retriveResult(microgrid_data,case,model):
    microgrid_device = case.device
    N_T = case.NumOfTime
    T = model.T
    step = 24 / N_T
    N_es = case.getKey(electricStorage)
    N_absc = case.getKey(absorptionChiller)
    N_bol = case.getKey(boiler)
    N_cs = case.getKey(coldStorage)
    N_ac = case.getKey(airConditioner)
    N_gt = case.getKey(gasTurbine)
    N_pv = case.getKey(PV)
    acLoad = microgrid_data['交流负荷'].tolist()
    dcLoad = microgrid_data['直流负荷'].tolist()
    pv_output = microgrid_data['光伏出力'].tolist()
    microgrid_device['ut'].buy_price = microgrid_data['电价'].tolist()
    cold_load = microgrid_data['冷负荷'].tolist()
    water_heat_load = microgrid_data['热水负荷'].tolist()
    steam_heat_load = microgrid_data['蒸汽负荷'].tolist()
    ts = pd.date_range('2017/8/28 00:00:00', periods=96, freq='15min')
    df = pd.DataFrame()
    df['交流负荷'] = pd.Series([acLoad[t] for t in T],index=T)
    df['直流负荷'] = pd.Series([dcLoad[t] for t in T],index=T)
    df['冷负荷'] = microgrid_data['冷负荷'].loc[T]
    df['蒸汽负荷'] =  microgrid_data['蒸汽负荷'].loc[T]
    df['电价'] = pd.Series(microgrid_device['ut'].buy_price).loc[T]
    df['购电功率'] = pd.Series([value(model.utility_power[t]) for t in T],index=T)
    df['购热功率'] = pd.Series([value(model.buy_heat[t]) for t in T],index=T)
    df['交流侧逆变器功率'] = pd.Series([value(model.inv_ac[t]) for t in T],index=T)
    df['直流侧逆变器功率'] = pd.Series([value(model.inv_dc[t]) for t in T],index=T)
    for es in N_es:
        df[es + '电池电量'] = pd.Series([value(model.es_energy[es, t]) for t in T],index=T)
        df[es + '电储能充电功率'] = pd.Series([value(model.es_power_in[es, t]) for t in T],index=T)
        df[es + '电储能放电功率'] = pd.Series([value(model.es_power_out[es, t]) for t in T],index=T)
    for gt in N_gt:
        df[gt + '机组出力'] = pd.Series([value(model.gt_power[gt, t]) for t in T],index=T)
        df[gt + '余热锅炉中品位热功率'] = pd.Series(
            [value(model.gt_power[gt, t]) * microgrid_device[gt].HER * microgrid_device[gt].heat_recycle for t
             in T],index=T)
        df[gt + '余热锅炉低品位热功率'] = pd.Series(
            [value(model.gt_power[gt, t]) * microgrid_device[gt].HER * microgrid_device[gt].low_heat_recycle for t
             in T], index=T)
    df['光伏出力'] = pd.Series(pv_output)
    for ac in N_ac:
        df[ac + '空调制冷耗电功率'] = pd.Series([value(model.ac_power[ac, t]) for t in T],index=T)
        df[ac + '空调制冷功率'] = df[ac + '空调制冷耗电功率'] * microgrid_device[ac].EER
    for cs in N_cs:
        df[cs + '冰蓄冷耗电功率'] = pd.Series([value(model.cs_power[cs, t]) for t in T],index=T)
        df[cs + '冰蓄冷储冷功率'] = pd.Series([value(model.cs_cold_in[cs, t]) for t in T],index=T)
        df[cs + '冰蓄冷供冷功率'] = pd.Series([value(model.cs_cold_out[cs, t]) for t in T],index=T)
        df[cs + '冰蓄冷制冷机直接供冷耗电功率'] = df[cs + '冰蓄冷耗电功率'] * microgrid_device[cs].EER - df[cs + '冰蓄冷储冷功率']
        df[cs + '冰蓄冷储冷量'] = pd.Series([value(model.cs_cold_stored[cs, t]) for t in T],index=T)
    for absc in N_absc:
        df[absc + '吸收式制冷机制冷功率'] = pd.Series([value(model.absc_heat_in[absc, t]) for t in T],index=T) * \
                                  microgrid_device[absc].COP_htc
    for bol in N_bol:
        df[bol + '燃气锅炉热功率'] = pd.Series([value(model.bol_power[bol, t]) for t in T],index=T)
    df['中品位热功率'] = pd.Series([value(model.buy_heat[t]) for t in T],index=T)
    #df['低品位热功率'] = pd.Series([value(model.low_heat[t]) for t in T],index=T) + pd.Series([value(model.medium_heat[t]) for t in T],index=T)
    '''demond response'''
    try:
        if model.mode == 'E':
            df['期望电功率'] = pd.Series(model.P_ref)
        elif model.mode == 'H':
            df['期望热功率'] = pd.Series(model.H_ref)
            df['可调负荷增加热功率']=pd.Series([value(model.DRHeatLoad[t] for t in model.peak)],index=T)
    except Exception as e:
        pass
    return df
def extendedResult(result):
    writer = pd.ExcelWriter('extenedResult.xlsx')
    sheet1 = pd.DataFrame()
    sheet1['电负荷'] = -result['交流负荷']-result['直流负荷']
    sheet1['购电功率'] = result['购电功率']
    sheet1['光伏出力'] = result['光伏出力']
    sheet1['电储能放电功率'] = df_sum(result,[col for col in result.columns if '电储能放电功率' in col])
    sheet1['燃气轮机发电功率'] = df_sum(result,[col for col in result.columns if '机组出力' in col])
    sheet1['冰蓄冷耗电功率'] = -df_sum(result,[col for col in result.columns if '冰蓄冷耗电功率' in col])
    sheet1['电储能充电功率'] = -df_sum(result,[col for col in result.columns if '电储能充电功率' in col])
    sheet1['空调制冷耗电功率'] = -df_sum(result,[col for col in result.columns if '空调制冷耗电功率' in col])
    sheet1['电价'] =result['电价']
    sheet1.to_excel(writer,sheet_name='电平衡优化调度结果')
    plt.figure(1)
    plt.rcParams['font.sans-serif'] = ['SimHei']
    load, = plt.plot(-sheet1['电负荷'],linewidth=3.0, linestyle='--', label='电负荷')
    sheet1colors = ['#f4f441','#42f486','#f412ee','#ff8000','#41b8f4','#408080','#7f41f4']
    plt.bar(result.index.values.tolist(),sheet1['购电功率'],color = '#f4f441')
    plt.bar(result.index.values.tolist(),sheet1['燃气轮机发电功率'],bottom=sheet1['购电功率'],color = '#42f486')
    plt.bar(result.index.values.tolist(),sheet1['电储能放电功率'],bottom=sheet1['燃气轮机发电功率']+sheet1['购电功率'],color = '#f442ee')
    plt.bar(result.index.values.tolist(), sheet1['光伏出力'], bottom=sheet1['燃气轮机发电功率'] + sheet1['购电功率']+sheet1['电储能放电功率'],
            color='#ff8000')
    plt.bar(result.index.values.tolist(),sheet1['电储能充电功率'],color = '#41b8f4')
    plt.bar(result.index.values.tolist(),sheet1['冰蓄冷耗电功率'],bottom=sheet1['电储能充电功率'],color = '#408080')
    plt.bar(result.index.values.tolist(),sheet1['空调制冷耗电功率'],bottom=sheet1['冰蓄冷耗电功率']+sheet1['电储能充电功率'],color ='#7f41f4' )
    first_legend = plt.legend([load],('电负荷',))
    ax = plt.gca().add_artist(first_legend)
    plt.legend([mpatches.Patch(color = c) for c in sheet1colors],['购电功率','燃气轮机发电功率','电储能放电功率','光伏出力','电储能充电功率','冰蓄冷耗电功率','空调制冷耗电功率'])
    plt.xlabel('时间')
    plt.ylabel('功率(kW)')
    plt.show()
    '''----------------华丽的分割线--------------------'''
    sheet2 = pd.DataFrame()
    sheet2['空调制冷功率'] = df_sum(result,[col for col in result.columns if '空调制冷功率' in col])
    sheet2['冰蓄冷供冷功率'] = df_sum(result,[col for col in result.columns if '冰蓄冷供冷功率' in col]) + df_sum(result,[col for col in result.columns if '冰蓄冷制冷机直接供冷耗电功率' in col])
    sheet2['吸收式制冷机制冷功率'] = df_sum(result,[col for col in result.columns if '吸收式制冷机制冷功率' in col])
    sheet2['冷负荷'] = -result['冷负荷']
    sheet2['电价'] = result['电价']
    sheet2.to_excel(writer, sheet_name='冷平衡优化调度结果')
    plt.figure(2)
    plt.rcParams['font.sans-serif'] = ['SimHei']
    load, = plt.plot(-sheet2['冷负荷'], linewidth=3.0, linestyle='--', label='冷负荷')
    sheet2colors = ['#f4f441', '#42f486', '#f442ee']
    #plt.stackplot(result.index.values.tolist(), sheet2['空调制冷功率'], sheet2['冰蓄冷供冷功率'], sheet2['吸收式制冷机制冷功率'],colors=sheet2colors)
    plt.bar(result.index.values.tolist(), sheet2['空调制冷功率'], color='#f4f441')
    plt.bar(result.index.values.tolist(), sheet2['冰蓄冷供冷功率'], bottom=sheet2['空调制冷功率'], color='#42f486')
    plt.bar(result.index.values.tolist(), sheet2['吸收式制冷机制冷功率'], bottom=sheet2['冰蓄冷供冷功率'] + sheet2['空调制冷功率'],color='#f442ee')
    first_legend = plt.legend([load], ('冷负荷',))
    ax = plt.gca().add_artist(first_legend)
    plt.legend([mpatches.Patch(color = c) for c in sheet1colors],['空调制冷功率','冰蓄冷供冷功率','吸收式制冷机制冷功率'])
    plt.xlabel('时间')
    plt.ylabel('功率(kW)')
    plt.show()
    '''----------------华丽的分割线--------------------'''
    sheet3 = pd.DataFrame()
    sheet3['冰蓄冷供冷功率'] = df_sum(result, [col for col in result.columns if '冰蓄冷供冷功率' in col])+ df_sum(result,[col for col in result.columns if '冰蓄冷制冷机直接供冷耗电功率' in col])
    sheet3['空调制冷功率'] = df_sum(result, [col for col in result.columns if '空调制冷功率' in col])
    sheet3['吸收式制冷机制冷功率'] = df_sum(result, [col for col in result.columns if '吸收式制冷机制冷功率' in col])
    sheet3['冷负荷'] = result['冷负荷']
    sheet3['电价'] = result['电价']
    sheet3.to_excel(writer, sheet_name='不考虑热品位冷平衡')
    '''----------------华丽的分割线--------------------'''
    sheet4 = pd.DataFrame()
    sheet4['购热功率'] = result['购热功率']
    sheet4['余热锅炉回收热功率'] = df_sum(result,[col for col in result.columns if '余热锅炉' in col])
    sheet4['吸收式制冷机耗热功率'] = df_sum(result, [col for col in result.columns if '吸收式制冷机制冷功率' in col])/0.8
    sheet4['蒸汽驱动负荷'] = -result['蒸汽负荷']
    sheet4['热负荷'] = sheet4['蒸汽驱动负荷']
    sheet4['电价'] = result['电价']
    sheet4.to_excel(writer, sheet_name='不考虑热品位热平衡')
    '''----------------华丽的分割线--------------------'''
    sheet5 = pd.DataFrame()
    sheet5['购热功率'] = result['购热功率']
    sheet5['余热锅炉中品位热功率'] = df_sum(result,[col for col in result.columns if '余热锅炉中品位热功率' in col])
    sheet5['余热锅炉低品位热功率'] =df_sum(result,[col for col in result.columns if '余热锅炉低品位热功率' in col])
    sheet5['蒸汽回收低品位热'] = H2M * result['蒸汽负荷']
    sheet5['中品位热功率'] = result['中品位热功率']
    #sheet5['低品位热功率'] = result['低品位热功率']
    sheet5['蒸汽驱动负荷'] = -result['蒸汽负荷']
    sheet5['热负荷'] = sheet5['蒸汽驱动负荷']
    sheet5['吸收式制冷机耗热功率'] = -df_sum(result, [col for col in result.columns if '吸收式制冷机制冷功率' in col]) / 0.8
    sheet5['吸收式制冷机制冷功率'] = df_sum(result, [col for col in result.columns if '吸收式制冷机制冷功率' in col])
    sheet5.to_excel(writer, sheet_name='考虑热品位热平衡')
    plt.figure(3)
    plt.rcParams['font.sans-serif'] = ['SimHei']
    load, = plt.plot(-sheet5['热负荷'], linewidth=3.0, linestyle='--', label='热负荷')
    sheet5colors = ['#f4f441', '#42f486', '#f442ee']
    #plt.stackplot(result.index.values.tolist(), sheet5['购热功率'], sheet5['余热锅炉中品位热功率'], sheet5['余热锅炉低品位热功率'],sheet5['蒸汽回收低品位热'],sheet5['吸收式制冷机耗热功率'],
                  #colors=sheet5colors)
    plt.bar(result.index.values.tolist(), sheet5['购热功率'], color='#f4f441')
    plt.bar(result.index.values.tolist(), sheet5['余热锅炉中品位热功率'], bottom=sheet5['购热功率'], color='#42f486')
    first_legend = plt.legend([load], ('蒸汽热负荷',))
    ax = plt.gca().add_artist(first_legend)
    plt.legend([mpatches.Patch(color=c) for c in sheet5colors], ['购热功率', '余热锅炉中品位热功率'])
    plt.xlabel('时间')
    plt.ylabel('功率(kW)')
    plt.show()
    '''----------------华丽的分割线--------------------'''
    sheet6 = pd.DataFrame()
    sheet6['蒸汽回收低品位热'] = H2M * result['蒸汽负荷']
    sheet6['余热锅炉低品位热功率'] = df_sum(result, [col for col in result.columns if '余热锅炉低品位热功率' in col])
    sheet6['吸收式制冷机耗热功率'] = -df_sum(result, [col for col in result.columns if '吸收式制冷机制冷功率' in col])/0.8
    sheet6['电价'] = result['电价']
    sheet6.to_excel(writer, sheet_name='吸收式制冷机耗热情况')
    plt.figure(4)
    plt.rcParams['font.sans-serif'] = ['SimHei']
    load, = plt.plot(-sheet6['吸收式制冷机耗热功率'], linewidth=3.0, linestyle='--', label='吸收式制冷机耗热功率')
    sheet6colors = ['#f4f441']
    plt.bar(result.index.values.tolist(), sheet6['蒸汽回收低品位热'], color='#f4f441')
    first_legend = plt.legend([load], ('ABSC耗热功率',))
    ax = plt.gca().add_artist(first_legend)
    plt.legend([mpatches.Patch(color=c) for c in sheet6colors], ['蒸汽回收低品位热'])
    plt.xlabel('时间')
    plt.ylabel('功率(kW)')
    plt.show()
    return
def responseModel(mdl,case,peak,amount,mode):
    model = copy.deepcopy(mdl)
    tmp = ConcreteModel()
    N_T = case.NumOfTime
    T = model.T
    microgrid_data = model.input
    step = 24 / N_T
    k1 = 1
    k2 = 1000
    peak = [t - model.T_range[0] for t in peak]
    model.peak = peak
    model.P_ref = list()
    model.H_ref = list()
    model.P_0 = [value(model.utility_power[t]) for t in T]
    model.H_0 = [value(model.buy_heat[t]) for t in T]
    if mode == 'E':
        for t in T:
            if t in peak:
                model.P_ref.append(value(model.utility_power[t]) - amount[t - peak[0]])
            else:
                model.P_ref.append(8000)
        model.H_ref = [1000]*len(T)
    elif mode == 'H':
        for t in T:
            if t in peak:
                model.H_ref.append(value(model.buy_heat[t]) + amount[t - peak[0]])
            else:
                model.H_ref.append(1000)
        model.P_ref = [8000]*len(T)
    model.DRHeatLoad = Var(peak,bounds=(case.device['DR_Heat_Load'].lower_bound,case.device['DR_Heat_Load'].upper_bound))
    steam_heat_load = microgrid_data['蒸汽负荷'].tolist()
    water_heat_load = microgrid_data['热水负荷'].tolist()
    # '''热损失惩罚函数'''
    # def wastingHeatPenalty(mdl):
    #     return 100000*sum(mdl.medium_heat[t] - steam_heat_load[t] for t in mdl.T)
    # model.wastingHeatPenalty = wastingHeatPenalty
    ''''更新目标函数'''
    def obj_response(mdl):
        if mode == 'E':
            return step * sum((mdl.utility_power[t] - mdl.P_ref[t]) for t in peak)
        elif mode == 'H':
            return step * sum((mdl.H_ref[t] - mdl.buy_heat[t]) for t in peak)
    tmp.obj = Objective(expr=obj_response(model))
    model.objective.set_value(k1*model.objective.expr + k2*tmp.obj.expr)

    ''''更新约束条件'''
    if mode == 'E':
        model.res_curve_u = Constraint(peak, rule=lambda mdl, t: mdl.utility_power[t] - mdl.P_ref[t] >= 0) #TODO 增加热约束
        model.pcc_limit = Constraint(set(T) - set(peak), rule=lambda mdl, t: mdl.utility_power[t] <= mdl.P_ref[t])
        model.heat_limit = Constraint(T, rule = lambda mdl,t: mdl.buy_heat[t] >= mdl.H_ref[t])
        model.eq_power = Constraint(peak, rule=lambda mdl,t: (mdl.P_0[peak[0]]-mdl.P_ref[peak[0]])*(mdl.utility_power[t]-mdl.P_0[t]) \
                                                             == (mdl.P_0[t]-mdl.P_ref[t])*(mdl.utility_power[peak[0]]-mdl.P_0[peak[0]]))
    elif mode == 'H':
        model.res_curve_u = Constraint(peak, rule=lambda mdl, t: mdl.buy_heat[t] - mdl.H_ref[t] <= 0) #TODO 增加电约束
        model.heat_limit = Constraint(set(T) - set(peak), rule=lambda mdl, t: mdl.buy_heat[t] >= mdl.H_ref[t])
        model.pcc_limit = Constraint(T, rule=lambda mdl, t: mdl.utility_power[t] <= mdl.P_ref[t])
        model.eq_power = Constraint(peak, rule=lambda mdl, t: (mdl.H_0[peak[0]] - mdl.H_ref[peak[0]]) * (
            mdl.buy_heat[t] - mdl.H_0[t]) \
                                                              == (mdl.H_0[t] - mdl.H_ref[t]) * (mdl.buy_heat[peak[0]] - mdl.H_0[peak[0]]))
        del model.HPB2
        del model.HPB2_index
        del model.HPB3
        del model.HPB3_index
        # del model.HPB4
        # del model.HPB4_index
        N_absc = model.case.getKey(absorptionChiller)
        # def low_heat_enough_or_not(b, t, indicator):
        #     m = b.model()
        #     if indicator == 0:  # low heat is not enough
        #         b.low_heat_state = Constraint(expr=m.low_heat[t] <= water_heat_load[t] + sum(m.absc_heat_in[n_absc, t] for n_absc in N_absc))
        #         b.HPB2 = Constraint(T,rule=lambda mdl, t: m.medium_heat[t] + m.low_heat[t]==
        #                                                       steam_heat_load[t] + m.DRHeatLoad[t] + water_heat_load[t] + sum(m.absc_heat_in[n_absc, t] for n_absc in N_absc) if t in peak else
        #         m.medium_heat[t] == steam_heat_load[t] + water_heat_load[t] + sum(m.absc_heat_in[n_absc, t] for n_absc in N_absc))
        #     else:
        #         b.low_heat_state = Constraint(expr=m.low_heat[t] >= water_heat_load[t] + sum(m.absc_heat_in[n_absc, t] for n_absc in N_absc))
        #         b.HPB2 = Constraint(T,rule=lambda mdl, t: m.medium_heat[t] + m.low_heat[t] == steam_heat_load[t] + m.DRHeatLoad[t] if t in peak else
        #         m.medium_heat[t] >= steam_heat_load[t])
        # model.low_heat_enough_or_not = Disjunct(T, [0, 1], rule=low_heat_enough_or_not)
        #
        # def low_heat_enough_disjunct(mdl, t):
        #     return [mdl.low_heat_enough_or_not[t, 0], mdl.low_heat_enough_or_not[t, 1]]
        #
        # model.low_heat_enough_disjunct = Disjunction(T, rule=low_heat_enough_disjunct)
        model.HPB2_1 = Constraint(T,rule = lambda mdl,t:mdl.medium_heat[t] >= steam_heat_load[t] + mdl.DRHeatLoad[t] if t in peak else
                                  mdl.medium_heat[t] >= steam_heat_load[t])
        model.HPB2_2 = Constraint(T,rule = lambda mdl,t:mdl.medium_heat[t] <= steam_heat_load[t] + mdl.DRHeatLoad[t] + sum(mdl.absc_heat_in[n_absc, t] for n_absc in N_absc) if t in peak else
                                  Constraint.Skip)
        model.HPB3 = Constraint(T,rule=lambda mdl,t:mdl.low_heat[t] == H2M*(steam_heat_load[t] + mdl.DRHeatLoad[t]) if t in peak else
        model.low_heat[t] == H2M * (steam_heat_load[t]))

    model.mode = mode
    xfrm = TransformationFactory('gdp.chull')
    xfrm.apply_to(model)
    if mode == 'E':
        solver = SolverFactory('glpk')
        solver.solve(model)
    elif mode == 'H':
        try:
            solver = SolverFactory('gurobi')
            solver.solve(model)
        except Exception:
            solver = SolverManagerFactory('neos')
            solver.solve(model, solver=SolverFactory('cplex'))
    return model
#TODO 补充最大可调容量
def getMaxAmount(mdl,case,peak,amount,mode):
    model = responseModel(mdl,case,peak,amount,mode)
    if mode == 'E':
        MaxAmount = [model.P_0[t-model.T[0]] - value(model.utility_power[t]) for t in peak]
    elif mode == 'H':
        MaxAmount = [- model.H_0[t-model.T[0]] + value(model.buy_heat[t]) for t in peak]
    else:
        MaxAmount = 0
    return (model,MaxAmount)
#TODO 补充完整日内修正模型
def DayInModel(microgrid_data, case, refE, refH, refSS, peak, T_range):
    N_bol = case.getKey(boiler)
    N_gt = case.getKey(gasTurbine)
    peak_shifted = [t - microgrid_data['dtime'].loc[T_range[0]] for t in peak]
    optimalDispatch = DayAheadModel(microgrid_data =  microgrid_data, case = case,T_range=T_range)
    T = optimalDispatch.T
    '''日内启停约束'''
    optimalDispatch.bolNoExtraSS = Constraint(N_bol, T,rule= lambda mdl,i,t:mdl.bol_state[i,t] == refSS[i][t])
    optimalDispatch.gtNoExtraSS = Constraint(N_gt, T,rule= lambda mdl,i,t:mdl.gt_state[i,t] == refSS[i ][t])
    '''违约成本'''
    optimalDispatch.EBauxvar = Var(T)
    optimalDispatch.HBauxvar = Var(T)
    fee_per_watt = 800
    def ElecBreakFee(mdl):
        if len(refE) == 0:
            return 0
        else:
            div = sum(mdl.EBauxvar[t] for t in peak_shifted)
            return fee_per_watt * div
    def HeatBreakFee(mdl):
        if len(refH) == 0:
            return 0
        else:
            div = sum(mdl.HBauxvar[t] for t in peak_shifted)
            return fee_per_watt * div
    if len(refE) > 0:
        optimalDispatch.EBcons1= Constraint(T,rule=lambda mdl,t:mdl.EBauxvar[t] >= mdl.utility_power[t] - refE[t])
        optimalDispatch.EBcons2 = Constraint(T, rule=lambda mdl, t: mdl.EBauxvar[t] >= - mdl.utility_power[t] + refE[t])
    if len(refH) > 0:
        optimalDispatch.HBcons1 = Constraint(T, rule=lambda mdl, t: mdl.HBauxvar[t] >= mdl.buy_heat[t] - refH[t])
        optimalDispatch.HBcons2 = Constraint(T, rule=lambda mdl, t: mdl.HBauxvar[t] >= - mdl.buy_heat[t] + refH[t])
    try:
        del optimalDispatch.objective
    except Exception:
        pass
    optimalDispatch.objective = Objective(rule=lambda mdl:optimalDispatch.obj_Economical(mdl)+ElecBreakFee(mdl)+HeatBreakFee(mdl))
    return optimalDispatch#TODO 补充完整日内修正模型

def df_sum(df,cols):
    newdf = pd.Series([0]*df.__len__(),index=df[cols[0]].index)
    for col in cols:
        newdf = newdf + df[col]
    return newdf
