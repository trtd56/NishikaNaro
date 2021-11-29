# -*- coding: utf-8 -*-
"""なろう_GBDT.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1h9bUAO4pVZhnraZECmg9l8BHf2sBchz0

# 小説家になろう ブクマ数予測 \~”伸びる”タイトルとは？\~

## Memo
- 文章ベクトル
- kwごとのtarget

### done
- PCA
- 単純なdate target
- pseudoでtestのtargetを修正
- userごとの修正
- dateのshift
- kw is none
"""

from google.colab import drive
drive.mount('/content/drive')

!pip install transformers unidic-lite fugashi ipadic python-Levenshtein sentencepiece catboost ipywidgets
!pip install -U torch
!jupyter nbextension enable --py widgetsnbextension

"""## 共通設定"""

import gc
import os
import random
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

from datetime import datetime as dt
from tqdm.notebook import tqdm
from collections import defaultdict
from sklearn.preprocessing import OneHotEncoder
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import log_loss
import itertools
import Levenshtein
import pickle

from gensim.corpora.dictionary import Dictionary
from gensim.models import LdaModel, TfidfModel, CoherenceModel
from collections import defaultdict

# Models
import lightgbm as lgb
from sklearn.linear_model import LogisticRegression
from sklearn.decomposition import PCA

import re

from catboost import Pool
from catboost import CatBoostClassifier
from google.colab import output
output.enable_custom_widget_manager()
#output.disable_custom_widget_manager()

# Transformer
import torch
import torch.nn as nn
from torch.nn import functional as F
from torch.utils.data import Dataset, DataLoader

from transformers import get_cosine_schedule_with_warmup
from transformers import AutoConfig
from transformers import AutoTokenizer
from transformers import AutoModel
from transformers import AdamW

from sklearn.naive_bayes import GaussianNB
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier

device = torch.device("cuda")
scaler = torch.cuda.amp.GradScaler()

class GCF:
    EXP_NAME = 'exp080_target_encoding'

    INPUT_PATH = "/content/drive/MyDrive/Study/Nishika"
    FEATURES_PATH = f"{INPUT_PATH}/features"
    RESULT_PATH = f"{INPUT_PATH}/result"
    MODELS_PATH = f"{INPUT_PATH}/models/{EXP_NAME}"

    N_FOLDS = 5
    SEED = 0

    FEATURES = [
        "pc_or_k",
        "org_bin",
        "genre_ohe", "biggenre_ohe",
        "kw_ohe_50_norm",
        "kw_lda_50_norm",
        "yyyymm",
        "date2",
        "userid_over5",
        "userid_only_test",
        "url_count_3",
        "kikaku_v1",
        "autopost",
        "cumsum_v1",
        "user_target_v1",
        #"user_target_v2_leak_fix",
        #"genre_target_v1",
        "genre_target_v2",
        #"lda_thr00",
        "bin_target",
        "date_target_v1",
        #"date_diff_v1",
        "kw_none",
        #"user_target_pseudo_v1",
        #"user_window_pseudo_v1",
        #"kmeans20",
        #"kmeans_bert20",
        #"onehot_target_v1",
        "kmeans5",
        "kmeans_bert3",
        "onehot_target_v2",
        "user_target_v3",
    ]

train_df = pd.read_csv(f"{GCF.INPUT_PATH}/train.csv")
test_df = pd.read_csv(f"{GCF.INPUT_PATH}/test.csv")
sub_df = pd.read_csv(f"{GCF.INPUT_PATH}/sample_submission.csv")

test_df['fav_novel_cnt_bin'] = -1

"""## 学習"""

X_cate = pd.concat([pd.read_feather(f"{GCF.FEATURES_PATH}/train_{i}.ftr") for i in GCF.FEATURES], axis=1)
X_cate_test = pd.concat([pd.read_feather(f"{GCF.FEATURES_PATH}/test_{i}.ftr") for i in GCF.FEATURES], axis=1)

# year
X_cate['upload_year_norm'] = X_cate['upload_year'].map(lambda x: (x-2007)/14)
X_cate_test['upload_year_norm'] = X_cate_test['upload_year'].map(lambda x: (x-2007)/14)
# monthy
X_cate['month_sin'] = np.sin(2 * np.pi * X_cate['upload_month']/12)
X_cate['month_cos'] = np.cos(2 * np.pi * X_cate['upload_month']/12)
X_cate_test['month_sin'] = np.sin(2 * np.pi * X_cate_test['upload_month']/12)
X_cate_test['month_cos'] = np.cos(2 * np.pi * X_cate_test['upload_month']/12)
# hour
X_cate['hour_sin'] = np.sin(2 * np.pi * X_cate['hour']/24)
X_cate['hour_cos'] = np.cos(2 * np.pi * X_cate['hour']/24)
X_cate_test['hour_sin'] = np.sin(2 * np.pi * X_cate_test['hour']/24)
X_cate_test['hour_cos'] = np.cos(2 * np.pi * X_cate_test['hour']/24)
# day_of_week
X_cate['day_of_week_sin'] = np.sin(2 * np.pi * X_cate['day_of_week']/7)
X_cate['day_of_week_cos'] = np.cos(2 * np.pi * X_cate['day_of_week']/7)
X_cate_test['day_of_week_sin'] = np.sin(2 * np.pi * X_cate_test['day_of_week']/7)
X_cate_test['day_of_week_cos'] = np.cos(2 * np.pi * X_cate_test['day_of_week']/7)

# カテゴリ変数
# userid
userid_columns_1 = 'userid_over5'
userid_columns_2 = 'userid_only_test'

userid_train_1 = X_cate[userid_columns_1]
userid_test_1 = X_cate_test[userid_columns_1]
n_userid_1 = len(set(userid_train_1.tolist() + userid_test_1.tolist()))
userid_train_2 = X_cate[userid_columns_2]
userid_test_2 = X_cate_test[userid_columns_2]
n_userid_2 = len(set(userid_train_2.tolist() + userid_test_2.tolist()))
# カテゴリ数の辞書
n_cate_dic = {
    'n_userid_1': n_userid_1,
    'n_userid_2': n_userid_2,
}

# 不要カラム削除
drop_features = ['upload_year', 'upload_month', 'hour', 'day_of_week', userid_columns_1, userid_columns_2] + ['target_1', 'target_2', 'target_3'] 
X_cate = X_cate.drop(drop_features, axis=1)
X_cate_test = X_cate_test.drop(drop_features, axis=1)
n_features = X_cate.shape[1]

# userid
X_cate[userid_columns_2] = userid_train_2
X_cate_test[userid_columns_2] = userid_test_2
X_cate[userid_columns_1] = userid_train_1
X_cate_test[userid_columns_1] = userid_test_1


fav_novel_cnt_bin = train_df['fav_novel_cnt_bin'].values

X_cate = pd.concat([
                    X_cate,
                    #nn_output_train_df,
                    train_df[['genre', 'biggenre']]
], axis=1) #, 'keyword']]], axis=1)
X_cate_test = pd.concat([
                    X_cate_test,
                    #nn_output_test_df,
                    test_df[['genre', 'biggenre']]
    ], axis=1) #, 'keyword']]], axis=1)

category = ['genre', 'biggenre', userid_columns_1, userid_columns_2]

bert_vector_test = np.load(f"{GCF.FEATURES_PATH}/bert_vector_test.npy")
bert_vector_train = np.load(f"{GCF.FEATURES_PATH}/bert_vector_train.npy")

X_cate = pd.concat([X_cate, pd.DataFrame(bert_vector_train, columns=[f"bert_{i}" for i in range(768)]) ], axis=1)
X_cate_test = pd.concat([X_cate_test, pd.DataFrame(bert_vector_test, columns=[f"bert_{i}" for i in range(768)])], axis=1)

"""### LGBM"""

params = {
    'objective': 'multiclass',
    'num_classes': 5,
    'metric': 'multi_logloss',
    'num_leaves': 5,
    'max_depth': 7,
    "feature_fraction": 0.9,
    'subsample_freq': 1,
    "bagging_fraction": 0.8,
    'min_data_in_leaf': 20,
    'learning_rate': 0.1,
    "boosting": "gbdt",
    "lambda_l1": 0.1,
    "lambda_l2": 0.1,
    "verbosity": -1,
    "random_state": 42,
    "num_boost_round": 50000,
    "early_stopping_rounds": 100
}

feature_imp_lst = []
predicts = []
oof = np.zeros((len(fav_novel_cnt_bin), 5))
skf = StratifiedKFold(n_splits=GCF.N_FOLDS, random_state=GCF.SEED, shuffle=True).split(X_cate, fav_novel_cnt_bin)
for fold, (train_index, valid_index) in enumerate(skf):
    print(f"Fold-{fold}")

    X_train = X_cate.loc[train_index, :]
    X_valid = X_cate.loc[valid_index, :]
    y_train = fav_novel_cnt_bin[train_index]
    y_valid = fav_novel_cnt_bin[valid_index]

    train_data = lgb.Dataset(X_train, label=y_train)
    valid_data = lgb.Dataset(X_valid, label=y_valid)

    model = lgb.train(
        params,
        train_data, 
        categorical_feature = category,
        valid_names = ['train', 'valid'],
        valid_sets =[train_data, valid_data], 
        verbose_eval = 100,
    )

    feature_imp = pd.DataFrame(sorted(zip(model.feature_importance(), X_cate.columns)), columns=['importance', 'feature'])
    feature_imp_lst.append(feature_imp)

    pred_valid = model.predict(X_valid, num_iteration=model.best_iteration)
    oof[valid_index] = pred_valid

    pred_test = model.predict(X_cate_test, num_iteration=model.best_iteration)
    predicts.append(pred_test)

oof_score = log_loss(np.stack([np.eye(5)[i] for i in fav_novel_cnt_bin]).astype(int), oof)
print(f"OOF score = {oof_score}")

predicts_avg = sum(predicts)/5
sub_df[["proba_0","proba_1","proba_2","proba_3","proba_4"]] = predicts_avg

!mkdir -p /content/drive/MyDrive/Study/Nishika/models/exp150_lgbm_now_best
sub_df.to_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp150_lgbm_now_best/lgbm_test_preds.csv", index=None)

oof_df = pd.DataFrame(oof, columns=["proba_0","proba_1","proba_2","proba_3","proba_4"])
oof_df['ncode'] = train_df['ncode']
oof_df.to_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp150_lgbm_now_best/lgbm_oof_preds.csv", index=None)

lgb.plot_importance(model, figsize=(12,8), max_num_features=50, importance_type='gain')
plt.tight_layout()
plt.show()

"""### CatBoost"""

feature_imp_lst = []
predicts = []
oof = np.zeros((len(fav_novel_cnt_bin), 5))
skf = StratifiedKFold(n_splits=GCF.N_FOLDS, random_state=GCF.SEED, shuffle=True).split(X_cate, fav_novel_cnt_bin)
for fold, (train_index, valid_index) in enumerate(skf):
    print(f"Fold-{fold}")

    X_train = X_cate.loc[train_index, :]
    X_valid = X_cate.loc[valid_index, :]
    y_train = fav_novel_cnt_bin[train_index]
    y_valid = fav_novel_cnt_bin[valid_index]

    train_pool = Pool(X_train, y_train, cat_features=category) #, text_features=text_feat)
    valid_pool = Pool(X_valid, y_valid, cat_features=category) #, text_features=text_feat)

    model = CatBoostClassifier(
        random_seed=42,
        verbose=100,
        #learning_rate=0.01,
    )
    model.fit(train_pool,
            eval_set=valid_pool,    # 検証用データ
            early_stopping_rounds=10,  # 10回以上精度が改善しなければ中止
            use_best_model=True,       # 最も精度が高かったモデルを使用するかの設定
            plot=True)

    pred_valid = pred_valid = model.predict(X_valid, prediction_type='Probability')
    oof[valid_index] = pred_valid
        
    pred_test = model.predict(X_cate_test, prediction_type='Probability')
    predicts.append(pred_test)

    feature_imp = pd.DataFrame(sorted(zip(model.get_feature_importance(valid_pool, type="PredictionValuesChange"), X_cate.columns)),
                               columns=['importance', 'feature'])
    feature_imp_lst.append(feature_imp)

oof_score = log_loss(np.stack([np.eye(5)[i] for i in fav_novel_cnt_bin]).astype(int), oof)
print(f"OOF score = {oof_score}")

predicts_avg = sum(predicts)/5
sub_df[["proba_0","proba_1","proba_2","proba_3","proba_4"]] = predicts_avg

!mkdir -p /content/drive/MyDrive/Study/Nishika/models/exp149_cat_now_best
sub_df.to_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp149_cat_now_best/cat_test_preds.csv", index=None)

oof_df = pd.DataFrame(oof, columns=["proba_0","proba_1","proba_2","proba_3","proba_4"])
oof_df['ncode'] = train_df['ncode']
oof_df.to_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp149_cat_now_best/cat_oof_preds.csv", index=None)

"""### XGBoost"""

import xgboost as xgb

params = {
        'objective': 'multi:softprob',
        'silent':1,
        'random_state':42, 
        'eval_metric': 'mlogloss',
        'num_class': 5,
        'booster': 'dart',
        'alpha': 1,
    }
num_round = 500

predicts = []
oof = np.zeros((len(fav_novel_cnt_bin), 5))
skf = StratifiedKFold(n_splits=GCF.N_FOLDS, random_state=GCF.SEED, shuffle=True).split(X_cate, fav_novel_cnt_bin)
for fold, (train_index, valid_index) in enumerate(skf):
    print("Fold:", fold)
    X_train = X_cate.loc[train_index, :]
    X_valid = X_cate.loc[valid_index, :]
    y_train = fav_novel_cnt_bin[train_index]
    y_valid = fav_novel_cnt_bin[valid_index]
    #y_train = np.stack([np.eye(5)[i] for i in fav_novel_cnt_bin[train_index]])
    #y_valid = np.stack([np.eye(5)[i] for i in fav_novel_cnt_bin[valid_index]])

    dtrain = xgb.DMatrix(X_train, label=y_train)
    dvalid = xgb.DMatrix(X_valid, label=y_valid)
    dtest = xgb.DMatrix(X_cate_test)


    watchlist = [(dtrain, 'train'), (dvalid, 'eval')]#訓練データはdtrain、評価用のテストデータはdvalidと設定

    model = xgb.train(params,
                        dtrain,#訓練データ
                        num_round,#設定した学習回数
                        early_stopping_rounds=20,
                        evals=watchlist,
                        )

    y_pred_valid = model.predict(dvalid, ntree_limit = model.best_ntree_limit)
    #score = log_loss(np.stack([np.eye(5)[i] for i in fav_novel_cnt_bin[valid_index]]), y_pred_valid)
    #print(f"fold-{fold}: {score}")
    oof[valid_index, :] = y_pred_valid
    y_pred_test = model.predict(dtest, ntree_limit=model.best_ntree_limit)
    predicts.append(y_pred_test)

oof_score = log_loss(np.stack([np.eye(5)[i] for i in fav_novel_cnt_bin]), oof)
print("OOF:", oof_score)

predicts_avg = sum(predicts)/5
sub_df[["proba_0","proba_1","proba_2","proba_3","proba_4"]] = predicts_avg

!mkdir -p /content/drive/MyDrive/Study/Nishika/models/exp115_xgb_now_best
sub_df.to_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp115_xgb_now_best/xgb_test_preds.csv", index=None)

oof_df = pd.DataFrame(oof, columns=["proba_0","proba_1","proba_2","proba_3","proba_4"])
oof_df['ncode'] = train_df['ncode']
oof_df.to_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp115_xgb_now_best/xgb_oof_preds.csv", index=None)

"""## Stacking"""

#oof_xgb = pd.read_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp115_xgb_now_best/xgb_oof_preds.csv")
#oof_nn = np.load(f"{GCF.FEATURES_PATH}/nn_output_exp107_train.npy")
#pred_xgb = pd.read_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp115_xgb_now_best/xgb_test_preds.csv")
#pred_nn = np.load(f"{GCF.FEATURES_PATH}/nn_output_exp107_test.npy")

oof_lgbm = pd.read_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp108_lgbm_now_best/lgbm_oof_preds.csv")
oof_lgbm2 = pd.read_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp125_lgbm_now_best/lgbm_oof_preds.csv")  # with bert
oof_lgbm5 = pd.read_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp139_lgbm_now_best/lgbm_oof_preds.csv")

oof_cat = pd.read_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp109_cat_now_best/cat_oof_preds.csv")
oof_cat2 = pd.read_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp126_cat_now_best/cat_oof_preds.csv")  # with bert
oof_cat5 = pd.read_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp140_cat_now_best/cat_oof_preds.csv") 
oof_cat6 = pd.read_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp144_cat_now_best/cat_oof_preds.csv") 

oof_nn2 = np.load(f"{GCF.FEATURES_PATH}/nn_output_exp113_cls_token_train.npy")
oof_nn4 = np.load(f"{GCF.FEATURES_PATH}/nn_output_exp129_userid_2_train.npy")
oof_nn6 = np.load(f"{GCF.FEATURES_PATH}/nn_output_exp141_fix_leak_train.npy")
oof_nn7 = np.load(f"{GCF.FEATURES_PATH}/nn_output_exp142_no_ae_train.npy")
oof_nn8 = np.load(f"{GCF.FEATURES_PATH}/nn_output_exp143_rinna_train.npy")

pred_lgbm = pd.read_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp108_lgbm_now_best/lgbm_test_preds.csv")
pred_lgbm2 = pd.read_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp125_lgbm_now_best/lgbm_test_preds.csv")
pred_lgbm5 = pd.read_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp139_lgbm_now_best/lgbm_test_preds.csv")

pred_cat = pd.read_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp109_cat_now_best/cat_test_preds.csv")
pred_cat2 = pd.read_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp126_cat_now_best/cat_test_preds.csv")
pred_cat5 = pd.read_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp140_cat_now_best/cat_test_preds.csv")
pred_cat6 = pd.read_csv(f"/content/drive/MyDrive/Study/Nishika/models/exp144_cat_now_best/cat_test_preds.csv")

pred_nn2 = np.load(f"{GCF.FEATURES_PATH}/nn_output_exp113_cls_token_test.npy")
pred_nn4 = np.load(f"{GCF.FEATURES_PATH}/nn_output_exp129_userid_2_test.npy")
pred_nn6 = np.load(f"{GCF.FEATURES_PATH}/nn_output_exp141_fix_leak_test.npy")
pred_nn7 = np.load(f"{GCF.FEATURES_PATH}/nn_output_exp142_no_ae_test.npy")
pred_nn8 = np.load(f"{GCF.FEATURES_PATH}/nn_output_exp143_rinna_test.npy")

X = np.hstack([
               oof_lgbm.values[:, :-1],
               oof_lgbm2.values[:, :-1],
               oof_lgbm5.values[:, :-1],
               oof_cat.values[:, :-1],
               oof_cat2.values[:, :-1],
               oof_cat5.values[:, :-1],
               oof_cat6.values[:, :-1],
               #oof_xgb.values[:, :-1],
               #oof_nn,
               oof_nn2,
               #oof_nn3,
               oof_nn6,
               oof_nn7,
               oof_nn8,
])
#y = np.array([np.eye(5)[i] for i in train_df['fav_novel_cnt_bin'].values])
y = train_df['fav_novel_cnt_bin'].values
X_test = np.hstack([
                    pred_lgbm.values[:, 1:],
                    pred_lgbm2.values[:, 1:],
                    pred_lgbm5.values[:, 1:],
                    pred_cat.values[:, 1:],
                    pred_cat2.values[:, 1:],
                    pred_cat5.values[:, 1:],
                    pred_cat6.values[:, 1:],
                    #pred_xgb.values[:, 1:],
                    #pred_nn,
                    pred_nn2,
                    #pred_nn3,
                    pred_nn6,
                    pred_nn7,
                    pred_nn8,
])

oof = np.zeros((len(y), 5))
test_predicts = []
skf = StratifiedKFold(n_splits=GCF.N_FOLDS, random_state=GCF.SEED, shuffle=True).split(X, y)
for fold, (train_index, valid_index) in enumerate(skf):
    #clf = GaussianNB()
    clf = LogisticRegression(max_iter=1000)
    #clf = RandomForestClassifier()
    clf.fit(X[train_index, :], y[train_index])
    y_pred_valid = clf.predict_proba(X[valid_index, :])
    score = log_loss(np.stack([np.eye(5)[i] for i in y[valid_index]]), y_pred_valid)
    print(f"fold-{fold}: {score}")
    oof[valid_index, :] = y_pred_valid
    y_pred_test = clf.predict_proba(X_test)
    test_predicts.append(y_pred_test)

oof_score = log_loss(np.stack([np.eye(5)[i] for i in y]), oof)
print("OOF:", oof_score)
# Stacking_125_109_113_mean: 0.7087320009924825
# Stacking_125_109_113_mean: 0.706783320107957
# Stacking_108_125_109_126_113_mean:

sub_df[["proba_0","proba_1","proba_2","proba_3","proba_4"]] = np.stack(test_predicts).mean(0)
#sub_df[["proba_0","proba_1","proba_2","proba_3","proba_4"]] = np.median(np.stack(test_predicts), axis=0)

sub_df.to_csv('Stacking_add_rinna_cat_no_bert.csv', index=None)
# Stacking_leak_fix: 0.6804016172710027
# Stacking_leak_fix_add_142: 0.6801213449851731
# Stacking_add_rinna: 0.6801213449851731
# fix cat boost: 0.6801609376012752

sub_df[sub_df.values[:, 1:].max(1) > 0.9]

"""## Pseudo"""

pseudo = pd.read_csv("Stacking_108_125_109_126_113_mean.csv")

pseudo.to_csv('/content/drive/MyDrive/Study/Nishika/result/Stacking_108_125_109_126_113_mean.csv', index=None)

pd.DataFrame(zip(pseudo[pseudo.values[:, 1:].max(1) > 0.9]['ncode'].values, pseudo[pseudo.values[:, 1:].max(1) > 0.9].values[:, 1:].argmax(1)))

"""## PostProcessing"""

#sub1 = pd.read_csv("/content/drive/MyDrive/Study/Nishika/result/exp092_mlp1024.csv")
pseudo = pd.read_csv("/content/drive/MyDrive/Study/Nishika/result/exp099_mlp2.csv")

pseudo[pseudo.values[:, 1:].max(1)>0.9].values[:, 1:].argmax(1)

#sub2[sub1.values[:, 1:].argmax(1) != sub2.values[:, 1:].argmax(1)]

#sub1[sub1.values[:, 1:].argmax(1) != sub2.values[:, 1:].argmax(1)]

lst = []
for row in sub.values[4:, 1:]:
    idx = row.argmax()
    #if idx == 4:
    #if row[idx] > 0.9:
    #    row[idx] = 1.0
    #lst.append(row)
    break

plt.plot(row)

sub[['proba_0', 'proba_1', 'proba_2', 'proba_3', 'proba_4']] = np.array(lst)

#sub.to_csv('exp092_mlp1024_pp01.csv', index=None)

(pd.read_csv('exp092_mlp1024_pp01.csv').values[:, 1:] - pd.read_csv('exp092_mlp1024_pp02.csv').values[:, 1:]).mean()



